# ffmpeg_convert_h265_mp4
Scan media library files and convert all files to h256 and convert to mp4

Transcoding media files with efficiency and speed requires a combination of proper software, configuration, and optimization. One effective tool for this task is FFmpeg, a powerful multimedia framework that can decode, encode, transcode, mux, demux, stream, filter, and play almost anything that humans and machines have created.

To transcode your media library efficiently and recursively, you can use a Python script in conjunction with FFmpeg. Below is an example script that uses subprocess to call FFmpeg commands and os to traverse your media library recursively.

First, ensure you have FFmpeg installed on your system:
