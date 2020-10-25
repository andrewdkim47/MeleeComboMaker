# MeleeComboMaker

[markdown cheatsheet](https://guides.github.com/pdfs/markdown-cheatsheet-online.pdf)

## Summary

Takes in Slippi files, automatically identifies combos, combines them, and makes a combo video.

## Architecture:

1. User Submits Slippi Files as a .zip file
2. Program unzips and stores files
3. For each slippi file:
   - Use combination of computer vision / slippi data to isolate the time frames to clip
   - Convert just the time frame into an mp4
   - Save the new mp4
4. Combine generated mp4s
5. create downloadable link for new final mp4.

## Converting slippi files into a mp4

Steps to make this work

1. pip install moviepy
2. pip install natsort
3. python combineVids.py
4. final.mp4 is ready

## Combining mp4 files with Python
