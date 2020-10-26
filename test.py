
import slippi as slp
import os

# helpful links: 
# https://py-slippi.readthedocs.io/en/latest/index.html
# https://github.com/project-slippi/project-slippi
# https://medium.com/analytics-vidhya/parsing-slippi-files-into-pandas-dataframes-the-metadata-2efdfcb8562c

game = slp.Game('test_files/Game_20200928T195151.slp')

# list of frames from the match 
frames = game.frames

# get the specific pre/post info from a specific frame of a given match 
print(frames[125].ports[0].leader.pre)
print(frames[125].ports[0].leader.post)