'''
    Takes all the mp4 files generated
    and combines it and spits out the new mp4
'''

from moviepy.editor import *
import os
from natsort import natsorted

L =[]

# Delete final output if already exists
if os.path.exists("final.mp4"):
  os.remove("final.mp4")
if os.path.exists("finalTEMP_MPY_wvf_snd.mp3"):
  os.remove("finalTEMP_MPY_wvf_snd.mp3")

print("Combining Vids...")
for root, dirs, files in os.walk("generatedVids/"):

    #files.sort()
    files = natsorted(files)
    for file in files:
        if os.path.splitext(file)[1] == '.mp4':
            filePath = os.path.join(root, file)
            video = VideoFileClip(filePath)
            L.append(video)

print("concatenating videoclips...", L)
final_clip = concatenate_videoclips(L, method="compose")
final_clip.to_videofile("final.mp4", fps=24, remove_temp=False)