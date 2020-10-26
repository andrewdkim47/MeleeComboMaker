import slippi as slp
import json
import os
from enum import IntEnum

SLP_PATH = 'test_files/'

# hitstun ratio to initaite combo recording 
HST = 100

# How many frames to retain while detecing combos 
WINDOW = 60 * 4

# make sure to include 2 seconds before
PREPOST = 60 * 2 

# to record states of the game
class State(IntEnum): 
	NEUTRAL = 0
	COMBO = 1
	EDGEGUARD = 2
	DEAD = 3

# dictionary generation fcns
def actionstate_dict():
	temp = {}
	count = 0
	f = open("actionstates.txt", 'r')
	for line in f: 
		temp[count] = line.rstrip()
		count += 1
	return temp

def attack_dict(): 
	temp = {}
	count = 1
	f = open("attacks.txt", 'r')
	for line in f: 
		temp[count] = line.rstrip()
		count += 1
	return temp


# given a .slp file, detects combos
# assumes 2 player match 
def find_combos(slp_path):
	game = slp.Game(slp_path)

	# cut the first 123 frames since its game startup
	frames = game.frames[123:]
	sdict = actionstate_dict()
	adict = attack_dict()

	# numbers to keep track of 
	# char state: 
	p1_prev_state = frames[0].ports[0].leader.post.state
	p2_prev_state = frames[0].ports[1].leader.post.state
	# current hitstun frames
	p1_hs = 0.5
	p2_hs = 0.5
	# each player's last percent 
	p1_dam = 0
	p2_dam = 0
	# last attack landed
	p1_lal = None
	p2_lal = None

	# record rolling averages and stuff
	comboer = 0
	victim = 1
	window_hs = 0
	state = State.NEUTRAL
	combo_counter = 0

	# record when hits occured
	# key = frame, val = attack/combo start/stop
	hitmap = {}
	combomap = {}

	fcount = 0
	for f in frames: 
		# for now, just look @ fox since i know he's the one getting abused
		if f.ports[victim].leader.post.damage > p2_dam:
			p1_lal = f.ports[comboer].leader.post.last_attack_landed
			if p1_lal < 30: 
				hitmap[fcount] = (adict[p1_lal], f.ports[victim].leader.post.damage-p2_dam)
			else: 
				hitmap[fcount] = (sdict[p1_lal], f.ports[victim].leader.post.damage-p2_dam)

		p2_dam = f.ports[victim].leader.post.damage
		p2_hs = f.ports[victim].leader.post.hit_stun

		# rolling hitstun counter
		if fcount > WINDOW: 
			window_hs -= 1

		if f.ports[1].leader.post.hit_stun >= 1: 
			window_hs += 1

		if window_hs >= HST and state != State.COMBO: 
			state = State.COMBO
			combomap[fcount] = "combo started"

		else: 
			# check for which non-combo status 
			# if (neutral): 
			# if (edgeguard): 
			# if (offstage): 
			# if (reverse): 
			pass

		fcount += 1


	# write hitmap
	with open('ref/'+slp_path[-9:-4] + '_hitmap.json', 'w') as outfile: 
		json.dump(hitmap, outfile, indent = 4)



def record_moves(slp_path, filename):
	game = slp.Game(slp_path)
	frames = game.frames[123:]
	sdict = actionstate_dict()
	adict = attack_dict()

	# record the metadata of the game
	md = game.metadata
	# dict. to record new charstates
	# key = state, val = initial frame it was found in
	falco_states = {}
	fox_states = {}
	moves = {}

	falco_states["duration"] = md.duration
	fox_states["duration"] = md.duration

	count = 0
	for f in frames:
		falstate = f.ports[0].leader.post.state
		foxstate = f.ports[1].leader.post.state
		last_move = f.ports[0].leader.post.last_attack_landed

		if falstate not in falco_states.keys():
			falco_states[sdict[falstate]] = count
		if foxstate not in fox_states.keys():
			fox_states[sdict[foxstate]] = count

		if last_move != None and last_move not in moves.keys(): 
			# if moves in the common moves list: if not, pull from char_states
			if int(last_move) > 30: 
				moves[sdict[last_move]] = count
			else: 
				moves[adict[last_move]] = count

		count += 1

	# reorder by value 
	falco_states = {k: v for k, v in sorted(falco_states.items(), key = lambda item: item[1])}
	fox_states = {k: v for k, v in sorted(fox_states.items(), key = lambda item: item[1])}
	moves = {k: v for k, v in sorted(moves.items(), key = lambda item: item[1])}

	# export to a json file to look at 
	with open('ref/'+filename+'.json', 'w') as outfile: 
		json.dump(falco_states, outfile, indent=4)

	with open('ref/fox_'+filename+'.json', 'w') as outfile: 
		json.dump(fox_states, outfile, indent = 4)

	with open('ref/attacks_'+filename+'.json','w') as outfile: 
		json.dump(moves, outfile, indent = 4)


	# transforms the frame count to the IGT that you should be looking at 
	def frame_to_sec(frame): 
		# in total there are 60 * 60 * 8 frames total in a match
		total = 60 * 60 * 8 
		return total

# takes in the number of frames passed since beginning of the match, and returns
# time on the clock we should be looking @ for a specific frame
# note: fnum should be given with first 123 frames of the game cut out,
# so frame 0 = first actionable frame, not a startup (ready, go!) frame
def frame_to_sec(fnum): 
	last_dig = [9,8,7,4,3,1]
	passed = fnum // 60
	rem = fnum % 60
	final = 0

	mins = 7-(passed//60)
	secs = 60-(passed%60)
	if rem: 
		secs -= 1
	ms = 9 - (rem // 6) 
	if rem%6: 
		final = rem%6

	time = '0{}:{}.{}{}.... {} fames in'.format(mins, secs, ms, last_dig[final], final)
	return time


# for quickly testing random shit, if you need to see some values 
# currently: checks fox's status in run 1 
def test(): 	
	fp = SLP_PATH + 'run_2.slp'
	game = slp.Game(fp)
	frames = game.frames[123:]
	i = 0
	for f in frames: 
		print(f.ports[1].leader.post.hit_stun)
	print("done")

##### Main Functions #####

def main(): 
	# define paths 
	falcon = SLP_PATH + 'vs_falcon.slp'
	run1 = SLP_PATH + 'run_1.slp'
	run2 = SLP_PATH + 'run_2.slp'
	run3 = SLP_PATH + 'run_3.slp'
	run4 = SLP_PATH + 'run_4.slp'
	
	# # record moves
	# record_moves(run1)
	# record_moves(run2)
	# record_moves(run3)

	# # test 
	# find_combos(run1)
	# find_combos(run2)
	# find_combos(run3)
	# find_combos(run4)

	print(frame_to_sec(2500))



if __name__ == '__main__': 
	main()


