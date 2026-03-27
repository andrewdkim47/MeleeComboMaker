# MeleeComboMaker

[markdown cheatsheet](https://guides.github.com/pdfs/markdown-cheatsheet-online.pdf)
[slippi](https://slippi.gg/)

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

## New: slp2mp4 integration

You can now convert a replay directly in Python:

```python
from slp2mp4_tools import convert_slp_to_mp4

video_path = convert_slp_to_mp4(
    "jaeyooncode/test_files/falconpunch.slp",
    "generatedVids"
)
print(video_path)
```

To install the dependency:

```bash
pip install -r requirements.txt
```

Note: upstream `slp2mp4` currently requires Python 3.11+. If you're on an older
Python version, the helper will fall back to this repo's existing legacy
`slp2mp4/slp-to-mp4.py` script.

CLI helper script:

```bash
python backend/convert_replay.py jaeyooncode/test_files/falconpunch.slp -o generatedVids
```

Combo highlight pipeline (reuse a full-match render and trim clips):

```bash
python backend/render_combo_clips.py jaeyooncode/test_files/falconpunch.slp -o comboVids --full-video-path "generatedVids/test_files falconpunch.mp4"
```

## Converting slippi files into a mp4

[github link for slp to mp4](https://github.com/NunoDasNeves/slp-to-mp4?fbclid=IwAR0DRyjkg-HbA0rz7XPooypKh8LIazelM0JUepxtApwIaA8LRNol82ibVRg)

For windows:

1. [download slp to mp4 files](https://github.com/NunoDasNeves/slp-to-mp4)
2. go to terminal
3. download psutil: `pip install git+https://github.com/giampaolo/psutil.git`
   - or `sudo pip install --upgrade psutil`
4. download py-slippi: `pip install git+https://github.com/hohav/py-slippi.git`

## Combining mp4 files with Python

Steps to make this work

1. pip install moviepy
2. pip install natsort
3. python combineVids.py
4. final.mp4 is ready
