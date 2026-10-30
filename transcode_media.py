#!/usr/bin/env python3

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
        subprocess.run(['sudo', 'apt-get', 'install', '-y', 'python3'], check=True)
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

# Function to get user input for media library location
def get_media_location():
    while True:
        media_library_path = input("Enter the path to your media library: ").strip()
        if os.path.isdir(media_library_path):
            return media_library_path
        else:
            logging.error("Invalid directory path. Please enter a valid path.")

# Main function
def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Get user input for media library location
    media_library_path = get_media_location()
    
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
