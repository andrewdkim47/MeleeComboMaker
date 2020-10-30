import slippi as slp
import json
import os
import math
import matplotlib.pyplot as plt
from combos import actionstate_dict, attack_dict, record_moves

# tunable parameters for combos 
HST = 100
WINDOW = 1200

# equation : 
# score = (A*hsum^B + C*dsum^D) * E*moves
def comboscore(dsum, hsum): 
	# these are all tunable parameters; set them to 1 for now
	A = 1
	B = 0.4

	C = 1
	D = 1.25

	return A*(hsum**B) * C*(dsum**D)

def record_hs(path, plotname):
	game = slp.Game(path)
	frames = game.frames[123:]
	sdict = actionstate_dict()
	adict = attack_dict()

	p0_name = game.metadata.players[0].netplay_name
	p1_name = game.metadata.players[1].netplay_name

	# now try rolling average: 
	p0_wind = []
	p0_wsum = 0

	p1_wind = []
	p1_wsum = 0

	p0_dwind = []
	p0_dsum = 0
	p0_hp = 0

	p1_dwind = []
	p1_dsum = 0
	p1_hp = 0

	# things to analyze 
	p0_map = []
	p0_dmap = []

	p1_map = []
	p1_dmap = []

	p0_scores = []
	p1_scores = []

	fcount = []
	fc = 0

	for f in frames:
		fcount.append(fc)
		fc += 1 

		p0_ths = f.ports[0].leader.post.hit_stun
		p0_tdam = f.ports[0].leader.post.damage
		p1_ths = f.ports[1].leader.post.hit_stun 
		p1_tdam = f.ports[1].leader.post.damage

		# took damage 
		if p0_tdam > p0_hp: 
			p0_dwind.append(p0_tdam - p0_hp)
			p0_dsum += p0_tdam - p0_hp
		else: 
			p0_dwind.append(0)
		if p0_ths > 0.1: 
			p0_wsum += p0_ths
			p0_wind.append(p0_ths)
		else: 
			p0_wind.append(0)


		if p1_tdam > p1_hp:
			p1_dwind.append(p1_tdam - p1_hp)
			p1_dsum += p1_tdam - p1_hp
		else: 
			p1_dwind.append(0)
		if p1_ths > 0.1: 
			p1_wsum += p1_ths
			p1_wind.append(p1_ths)
		else: 
			p1_wind.append(0)


		# check lengths & adjust
		if(len(p0_wind) > WINDOW):
			p0_wsum -= p0_wind[0] 
			p0_wind.pop(0)
		if(len(p1_wind) > WINDOW):
			p1_wsum -= p1_wind[0] 
			p1_wind.pop(0)

		if(len(p0_dwind) > WINDOW): 
			p0_dsum -= p0_dwind[0]
			p0_dwind.pop(0)
		if(len(p1_dwind) > WINDOW): 
			p1_dsum -= p1_dwind[0]
			p1_dwind.pop(0)

		p0_map.append(p0_wsum)
		p0_dmap.append(p0_dsum)

		p1_map.append(p1_wsum)
		p1_dmap.append(p1_dsum)

		# calculate combo scores 
		# note: port 0's score is based on port 1's damage & hitstun values, as they
		# were a result of his attacks (same w/ port 2)
		p0_scores.append(comboscore(p1_dsum, p1_wsum))
		p1_scores.append(comboscore(p0_dsum, p0_wsum))


		# adjust hp for next frame
		p0_hp = p0_tdam
		p1_hp = p1_tdam

	fig, (ax1, ax2) = plt.subplots(2)
	ax1.plot(fcount, p0_map, label = p0_name)
	ax1.plot(fcount, p1_map, label = p1_name, color = 'red')
	ax1.set_title('hitstun map')
	ax1.legend()
	ax2.plot(fcount, p0_dmap, label = p0_name)
	ax2.plot(fcount, p1_dmap, label = p1_name, color = 'red')
	ax2.set_title('damage map')
	ax2.legend()
	fig.savefig('test_out/{}.png'.format(plotname))

	scorefig, ax = plt.subplots()
	ax.plot(fcount, p0_scores, label = p0_name)
	ax.plot(fcount, p1_scores, label = p1_name)
	ax.legend()
	scorefig.savefig('test_out/{}_cscores.png'.format(plotname))



#### MAIN FCSN ####

def main(): 
	run1 = 'test_files/run_1.slp'
	run2 = 'test_files/run_2.slp'
	run3 = 'test_files/run_3.slp'
	
	test = 'test_files/temp.slp'
	# grab and pummelled 
	grab = 'test_files/grab.slp'
	# waited first 15 seconds
	wait = 'test_files/wait.slp'

	rand1 = 'test_files/random_1.slp'

	record_hs(run2, 'run2_w={}'.format(WINDOW))
	#record_hs(run3, 'run3_w={}'.format(WINDOW))
	#record_hs(test, 'test_run_w={}'.format(WINDOW))
	#record_moves(test, 'test_run_w={}'.format(WINDOW))
	record_hs(rand1, 'rand_1_w={}'.format(WINDOW))

if __name__ == '__main__': 
	main()