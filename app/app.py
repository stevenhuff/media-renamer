from flask import Flask, render_template, request, jsonify, Response
import requests
import os
import re
import shutil
from queue import Queue
from threading import Lock

app = Flask(__name__)

OMDB_API_KEY = os.environ.get('OMDB_API_KEY')
OMDB_URL = "http://www.omdbapi.com/"

if not OMDB_API_KEY:
    raise ValueError("OMDB_API_KEY environment variable is not set")

# Base directories
BASE_DIR = '/media/queue'          # Updated mount point for consistency
MOVIE_DIR = '/media/movies'
SHOW_DIR = '/media/shows'

# Ensure destination directories exist
for dir_path in [MOVIE_DIR, SHOW_DIR]:
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

# SSE event queue
event_queue = Queue()
event_lock = Lock()

def send_event(event_type, message):
    with event_lock:
        event_queue.put(f"data: {event_type}:{message}\n\n")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/events')
def sse():
    def event_stream():
        while True:
            message = event_queue.get()
            yield message
            event_queue.task_done()
    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/folders', methods=['GET'])
def get_folders():
    try:
        if not os.path.exists(BASE_DIR):
            return jsonify({'success': False, 'error': 'Media directory not found', 'folders': []})
        
        folders = [f for f in os.listdir(BASE_DIR) 
                  if os.path.isdir(os.path.join(BASE_DIR, f)) and not f.startswith('.')]
        return jsonify({'success': True, 'folders': folders})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'folders': []})

@app.route('/files', methods=['POST'])
def get_files():
    folder_name = request.form['folder_name']
    folder_path = os.path.join(BASE_DIR, folder_name)
    try:
        if not os.path.exists(folder_path):
            return jsonify({'success': False, 'error': f"Folder {folder_path} does not exist", 'files': []})
        
        files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
        return jsonify({'success': True, 'files': files})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'files': []})

@app.route('/delete', methods=['POST'])
def delete_files():
    folder_name = request.form['folder_name']
    files_to_delete = request.form.getlist('files[]')
    folder_path = os.path.join(BASE_DIR, folder_name)
    
    try:
        if not os.path.exists(folder_path):
            error_msg = f"Folder {folder_path} does not exist"
            send_event('delete_error', error_msg)
            return jsonify({'success': False, 'error': error_msg})
        
        deleted_files = []
        for filename in files_to_delete:
            file_path = os.path.join(folder_path, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
                deleted_files.append(filename)
                send_event('delete_success', f"Deleted {filename} from {folder_path}")
            else:
                send_event('delete_error', f"File {filename} not found in {folder_path}")
        
        return jsonify({'success': True, 'deleted': deleted_files})
    except Exception as e:
        send_event('delete_error', str(e))
        return jsonify({'success': False, 'error': str(e)})

@app.route('/extras', methods=['POST'])
def move_to_extras():
    folder_name = request.form['folder_name']
    files_to_move = request.form.getlist('files[]')
    folder_path = os.path.join(BASE_DIR, folder_name)
    extras_path = os.path.join(folder_path, 'Extras')
    
    try:
        if not os.path.exists(folder_path):
            error_msg = f"Folder {folder_path} does not exist"
            send_event('extras_error', error_msg)
            return jsonify({'success': False, 'error': error_msg})
        
        if not os.path.exists(extras_path):
            os.makedirs(extras_path)
        
        moved_files = []
        for filename in files_to_move:
            src_path = os.path.join(folder_path, filename)
            dest_path = os.path.join(extras_path, filename)
            if os.path.isfile(src_path):
                shutil.move(src_path, dest_path)
                moved_files.append(filename)
                send_event('extras_success', f"Moved {filename} to Extras in {folder_path}")
            else:
                send_event('extras_error', f"File {filename} not found in {folder_path}")
        
        return jsonify({'success': True, 'moved': moved_files})
    except Exception as e:
        send_event('extras_error', str(e))
        return jsonify({'success': False, 'error': str(e)})

@app.route('/search_suggestions', methods=['POST'])
def search_suggestions():
    query = request.form['query']
    params = {
        'apikey': OMDB_API_KEY,
        's': query,
    }
    
    response = requests.get(OMDB_URL, params=params)
    if response.status_code == 200:
        data = response.json()
        if data.get('Response') == 'True':
            suggestions = [{
                'title': item['Title'],
                'year': item['Year'],
                'type': item['Type']
            } for item in data.get('Search', [])[:5]]
            return jsonify({'success': True, 'suggestions': suggestions})
    return jsonify({'success': False, 'suggestions': []})

@app.route('/search', methods=['POST'])
def search_movie():
    query = request.form['query']
    params = {
        'apikey': OMDB_API_KEY,
        't': query,
        'plot': 'short'
    }
    
    response = requests.get(OMDB_URL, params=params)
    if response.status_code == 200:
        data = response.json()
        if data.get('Response') == 'True':
            return jsonify({
                'success': True,
                'title': data['Title'],
                'year': data['Year'],
                'type': data['Type'],
                'metadata': data,
                'is_series': data['Type'] == 'series'
            })
    return jsonify({'success': False, 'error': 'Media not found'})

@app.route('/get_episode', methods=['POST'])
def get_episode():
    title = request.form['title']
    season = request.form['season']
    episode = request.form['episode']
    
    params = {
        'apikey': OMDB_API_KEY,
        't': title,
        'Season': season,
        'Episode': episode
    }
    
    response = requests.get(OMDB_URL, params=params)
    if response.status_code == 200:
        data = response.json()
        if data.get('Response') == 'True':
            return jsonify({'success': True, 'metadata': data})
    return jsonify({'success': False, 'error': 'Episode not found'})

@app.route('/rename', methods=['POST'])
def rename_files():
    folder_name = request.form['folder_path']
    title = request.form['title']
    year = request.form['year']
    metadata = request.form.get('metadata')
    is_series = request.form.get('is_series') == 'true'
    season = request.form.get('season')
    episode = request.form.get('episode')
    
    folder_path = os.path.join(BASE_DIR, folder_name)
    
    try:
        if not os.path.exists(folder_path):
            error_msg = f"Folder {folder_path} does not exist"
            send_event('rename_error', error_msg)
            return jsonify({'success': False, 'error': error_msg})
            
        send_event('rename_start', f"Renaming {folder_path}")
        clean_title = re.sub(r'[^\w\s-]', '', title).strip()
        
        if is_series and season and episode:
            import json
            meta = json.loads(metadata)
            season_folder = f"Season {season.zfill(2)}"
            new_folder_name = clean_title
            episode_title = meta.get('Title', '').replace('/', '-')
            new_filename_base = f"{clean_title} - s{season.zfill(2)}e{episode.zfill(2)}"
            if episode_title:
                new_filename_base += f" - {episode_title}"
            
            parent_dir = os.path.dirname(folder_path)
            show_path = os.path.join(parent_dir, new_folder_name)
            season_path = os.path.join(show_path, season_folder)
            
            if not os.path.exists(show_path):
                os.makedirs(show_path)
            if not os.path.exists(season_path):
                os.makedirs(season_path)
            
            for item in os.listdir(folder_path):
                os.rename(
                    os.path.join(folder_path, item),
                    os.path.join(season_path, item)
                )
            if not os.listdir(folder_path):
                os.rmdir(folder_path)
            
            final_path = season_path
        else:
            new_folder_name = f"{clean_title} ({year})"
            new_filename_base = f"{clean_title} ({year})"
            parent_dir = os.path.dirname(folder_path)
            final_path = os.path.join(parent_dir, new_folder_name)
            os.rename(folder_path, final_path)

        for filename in os.listdir(final_path):
            file_path = os.path.join(final_path, filename)
            if os.path.isfile(file_path):
                extension = os.path.splitext(filename)[1]
                quality = ''
                if '1080p' in filename.lower():
                    quality = ' - 1080p'
                elif '720p' in filename.lower():
                    quality = ' - 720p'
                elif '4k' in filename.lower():
                    quality = ' - 4K'
                
                new_filename = f"{new_filename_base}{quality}{extension}"
                new_file_path = os.path.join(final_path, new_filename)
                os.rename(file_path, new_file_path)
                
        send_event('rename_success', f"Renamed to: {final_path}")
        return jsonify({'success': True, 'new_path': final_path, 'is_series': is_series})
    except Exception as e:
        send_event('rename_error', str(e))
        return jsonify({'success': False, 'error': str(e)})

@app.route('/move', methods=['POST'])
def move_folder():
    folder_path = request.form['folder_path']
    destination = request.form['destination']
    
    try:
        if not os.path.exists(folder_path):
            error_msg = f"Source folder {folder_path} does not exist"
            send_event('move_error', error_msg)
            return jsonify({'success': False, 'error': error_msg})
        
        send_event('move_start', f"Moving {folder_path}")
        if destination == 'movie':
            dest_path = os.path.join(MOVIE_DIR, os.path.basename(folder_path))
        elif destination == 'show':
            dest_path = os.path.join(SHOW_DIR, os.path.basename(folder_path))
        else:
            error_msg = 'Invalid destination'
            send_event('move_error', error_msg)
            return jsonify({'success': False, 'error': error_msg})
        
        if os.path.exists(dest_path):
            error_msg = f"Destination {dest_path} already exists"
            send_event('move_error', error_msg)
            return jsonify({'success': False, 'error': error_msg})
        
        shutil.move(folder_path, dest_path)
        send_event('move_success', f"Moved to: {dest_path}")
        return jsonify({'success': True, 'new_path': dest_path})
    except Exception as e:
        send_event('move_error', str(e))
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)