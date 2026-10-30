Explanation
Media Library Path: Replace /path/to/your/media/library with the actual path to your media library.

Target Codec and Format: Define the target codec (target_codec) and format (target_format) for transcoding.

Transcoding a Single Media File: The transcode_file function uses subprocess.run to call FFmpeg with the appropriate options to transcode a single media file.

Transcoding a Directory Recursively: The transcode_directory function recursively walks through the media library directory, identifies media files, and calls transcode_file for each file.

Running the Script
To run the script, save it to a file (e.g., transcode_media.py) and execute it using Python:

Run
python transcode_media.py
This script will recursively transcode all media files in your media library using the specified codec and format. You can customize the target codec, format, and other FFmpeg options as needed.

Additional Optimizations
Parallel Transcoding: For better performance, you can use multiprocessing to run multiple FFmpeg processes in parallel. Here's an example using the concurrent.futures module:


Apply
from concurrent.futures import ThreadPoolExecutor

# Function to transcode a single media file
def transcode_file(input_file, output_file):
    try:
        subprocess.run(
            [
                'ffmpeg',
                '-i', input_file,
                '-c:v', target_codec,
                '-c:a', 'aac',
                '-b:a', '192k',
                '-movflags', '+faststart',
                output_file
            ],
            check=True
        )
        print(f"Transcoded {input_file} to {output_file}")
    except subprocess.CalledProcessError as e:
        print(f"Error transcoding {input_file}: {e}")

# Function to recursively transcode media files
def transcode_directory(directory):
    futures = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        for root, _, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                if file_path.lower().endswith(('.mkv', '.avi', '.mov', '.mp4', '.wmv', '.flv', '.mpeg')):
                    output_file = file_path.rsplit('.', 1)[0] + f'.{target_format}'
                    if file_path != output_file:
                        futures.append(executor.submit(transcode_file, file_path, output_file))
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"Error: {e}")

# Start the transcoding process
transcode_directory(media_library_path)
This script uses a thread pool to run up to 4 FFmpeg processes in parallel, which can significantly speed up the transcoding process.

Error Handling and Logging: Add more sophisticated error handling and logging to ensure the script can handle various edge cases and provide useful feedback during the transcoding process.

By following these steps, you can efficiently and recursively transcode your media library using Python and FFmpeg.






write this into one script , install any missing dependencies and make it self healing

Certainly! Below is a complete script that installs any missing dependencies, checks for the presence of FFmpeg, and transcodes your media library recursively. The script also includes basic error handling and logging.


Apply
import os
import subprocess
import sys
import logging

# Path to your media library
media_library_path = '/path/to/your/media/library'

# Target codec and format
target_codec = 'libx264'
target_format = 'mp4'

# Function to install missing dependencies
def install_dependencies():
    try:
        subprocess.run(['sudo', 'apt-get', 'update'], check=True)
        subprocess.run(['sudo', 'apt-get', 'install', '-y', 'ffmpeg'], check=True)
        logging.info("Dependencies installed successfully.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error installing dependencies: {e}")
        sys.exit(1)

# Function to transcode a single media file
def transcode_file(input_file, output_file):
    try:
        subprocess.run(
            [
                'ffmpeg',
                '-i', input_file,
                '-c:v', target_codec,
                '-c:a', 'aac',
                '-b:a', '192k',
                '-movflags', '+faststart',
                output_file
            ],
            check=True
        )
        logging.info(f"Transcoded {input_file} to {output_file}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error transcoding {input_file}: {e}")

# Function to recursively transcode media files
def transcode_directory(directory):
    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            if file_path.lower().endswith(('.mkv', '.avi', '.mov', '.mp4', '.wmv', '.flv', '.mpeg')):
                output_file = file_path.rsplit('.', 1)[0] + f'.{target_format}'
                if file_path != output_file:
                    transcode_file(file_path, output_file)

# Main function
def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Check if FFmpeg is installed
    try:
        subprocess.run(['ffmpeg', '-version'], check=True)
        logging.info("FFmpeg is installed.")
    except subprocess.CalledProcessError:
        logging.info("FFmpeg is not installed. Installing now...")
        install_dependencies()
    
    # Transcode the media library
    transcode_directory(media_library_path)

if __name__ == "__main__":
    main()
Explanation
Dependencies and FFmpeg Check:

The script checks if FFmpeg is installed and installs it if it's not.
The install_dependencies function uses subprocess.run to update the package list and install FFmpeg.
Transcoding Functions:

The transcode_file function transcodes a single media file using FFmpeg.
The transcode_directory function recursively walks through the media library and calls transcode_file for each media file.
Logging:

The script uses Python's logging module to log information, warnings, and errors.
Main Function:

The main function orchestrates the script by checking for FFmpeg, installing it if necessary, and then transcoding the media library.
Running the Script
To run the script, save it to a file (e.g., transcode_media.py) and execute it using Python:

Run
python transcode_media.py
This script will install any missing dependencies, check for the presence of FFmpeg, and transcode your media library recursively. It includes basic error handling and logging to ensure the script can handle various edge cases and provide useful feedback during the transcoding process.
