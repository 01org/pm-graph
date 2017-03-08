#!/usr/bin/python
#
# Tool for analyzing boot timing
# Copyright (c) 2013, Intel Corporation.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms and conditions of the GNU General Public License,
# version 2, as published by the Free Software Foundation.
#
# This program is distributed in the hope it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St - Fifth Floor, Boston, MA 02110-1301 USA.
#
# Authors:
#	 Todd Brandt <todd.e.brandt@linux.intel.com>
#
# Description:
#	 This tool is designed to assist kernel and OS developers in optimizing
#	 their linux stack's boot time. It creates an html representation of
#	 the kernel boot timeline up to the start of the init process.
#
#	 The following additional kernel parameters are required:
#		 (e.g. in file /etc/default/grub)
#		 GRUB_CMDLINE_LINUX_DEFAULT="... initcall_debug log_buf_len=16M ..."
#

# ----------------- LIBRARIES --------------------

import sys
import time
import os
import string
import re
import platform
from datetime import datetime, timedelta
from subprocess import call, Popen, PIPE
import analyze_suspend as aslib

# ----------------- CLASSES --------------------

# Class: SystemValues
# Description:
#	 A global, single-instance container used to
#	 store system values and test parameters
class SystemValues:
	version = 2.0
	hostname = 'localhost'
	testtime = ''
	kernel = ''
	dmesgfile = ''
	ftracefile = ''
	htmlfile = 'bootgraph.html'
	outfile = ''
	phoronix = False
	addlogs = False
	usecallgraph = False
	def __init__(self):
		if('LOG_FILE' in os.environ and 'TEST_RESULTS_IDENTIFIER' in os.environ):
			self.phoronix = True
			self.addlogs = True
			self.outfile = os.environ['LOG_FILE']
			self.htmlfile = os.environ['LOG_FILE']
		self.hostname = platform.node()
		self.testtime = datetime.now().strftime('%Y-%m-%d_%H:%M:%S')
		fp = open('/proc/version', 'r')
		val = fp.read().strip()
		fp.close()
		self.kernel = self.kernelVersion(val)
	def kernelVersion(self, msg):
		return msg.split()[2]
sysvals = SystemValues()

# Class: Data
# Description:
#	 The primary container for test data.
class Data:
	dmesg = {}  # root data structure
	start = 0.0 # test start
	end = 0.0   # test end
	dmesgtext = []   # dmesg text file in memory
	testnumber = 0
	idstr = ''
	html_device_id = 0
	stamp = 0
	valid = False
	initstart = 0.0
	boottime = ''
	phases = ['boot']
	def __init__(self, num):
		self.testnumber = num
		self.idstr = 'a'
		self.dmesgtext = []
		self.dmesg = {
			'boot': {'list': dict(), 'start': -1.0, 'end': -1.0, 'row': 0, 'color': '#dddddd'}
		}
	def deviceTopology(self):
		return ''
	def newAction(self, phase, name, pid, parent, start, end, drv):
		# new device callback for a specific phase
		self.html_device_id += 1
		devid = '%s%d' % (self.idstr, self.html_device_id)
		list = self.dmesg[phase]['list']
		length = -1.0
		if(start >= 0 and end >= 0):
			length = end - start
		i = 2
		origname = name
		while(name in list):
			name = '%s[%d]' % (origname, i)
			i += 1
		list[name] = {'name': name, 'start': start, 'end': end,
			'pid': pid, 'par': parent, 'length': length, 'row': 0,
			'id': devid, 'drv': drv }
		return name
	def deviceMatch(self, cg):
		list = self.dmesg['boot']['list']
		for devname in list:
			dev = list[devname]
			if(cg.start <= dev['start'] and
				cg.end >= dev['end']):
				dev['ftrace'] = cg
				return True
		return False
	def sortedDevices(self, phase):
		list = self.dmesg[phase]['list']
		slist = []
		tmp = dict()
		for devname in list:
			dev = list[devname]
			tmp[dev['start']] = devname
		for t in sorted(tmp):
			slist.append(tmp[t])
		return slist

# ----------------- FUNCTIONS --------------------

# Function: loadKernelLog
# Description:
#	 Load a raw kernel log from dmesg
def loadKernelLog():
	data = Data(0)
	data.dmesg['boot']['start'] = data.start = ktime = 0.0
	data.stamp = {
		'time': datetime.now().strftime('%B %d %Y, %I:%M:%S %p'),
		'host': sysvals.hostname,
		'mode': 'boot', 'kernel': ''}

	devtemp = dict()
	if(sysvals.dmesgfile):
		lf = open(sysvals.dmesgfile, 'r')
	else:
		lf = Popen('dmesg', stdout=PIPE).stdout
	for line in lf:
		line = line.replace('\r\n', '')
		idx = line.find('[')
		if idx > 1:
			line = line[idx:]
		m = re.match('[ \t]*(\[ *)(?P<ktime>[0-9\.]*)(\]) (?P<msg>.*)', line)
		if(not m):
			continue
		ktime = float(m.group('ktime'))
		if(ktime > 120):
			break
		msg = m.group('msg')
		data.end = data.initstart = ktime
		data.dmesgtext.append(line)
		if(ktime == 0.0 and re.match('^Linux version .*', msg)):
			if(not data.stamp['kernel']):
				data.stamp['kernel'] = sysvals.kernelVersion(msg)
			continue
		m = re.match('.* setting system clock to (?P<t>.*) UTC.*', msg)
		if(m):
			utc = int((datetime.now() - datetime.utcnow()).total_seconds())
			bt = datetime.strptime(m.group('t'), '%Y-%m-%d %H:%M:%S')
			bt = bt - timedelta(seconds=int(ktime)-utc)
			data.boottime = bt.strftime('%Y-%m-%d_%H:%M:%S')
			data.stamp['time'] = bt.strftime('%B %d %Y, %I:%M:%S %p')
			continue
		m = re.match('^calling *(?P<f>.*)\+.*', msg)
		if(m):
			devtemp[m.group('f')] = ktime
			continue
		m = re.match('^initcall *(?P<f>.*)\+.*', msg)
		if(m):
			data.valid = True
			f = m.group('f')
			if(f in devtemp):
				data.newAction('boot', f, 0, '', devtemp[f], ktime, '')
				data.end = ktime
				del devtemp[f]
			continue
		if(re.match('^Freeing unused kernel memory.*', msg)):
			break

	data.dmesg['boot']['end'] = data.end
	lf.close()
	return data

# Function: loadTraceLog
# Description:
#	 Check if trace is available and copy to a temp file
def loadTraceLog(data):
	# load the data to a temp file if none given
	if not sysvals.ftracefile:
		lib = aslib.sysvals
		aslib.rootCheck(True)
		if not lib.verifyFtrace():
			doError('ftrace not available')
		if lib.fgetVal('current_tracer').strip() != 'function_graph' or \
			'do_one_initcall' not in lib.fgetVal('set_graph_function'):
			doError('ftrace not configured for a boot callgraph')
		sysvals.ftracefile = '/tmp/boot_ftrace.%s.txt' % os.getpid()
		call('cat '+lib.tpath+'trace > '+sysvals.ftracefile, shell=True)
	if not sysvals.ftracefile:
		doError('No trace data available')

	# parse the trace log
	ftemp = dict()
	tp = aslib.TestProps()
	tp.setTracerType('function_graph')
	tf = open(sysvals.ftracefile, 'r')
	for line in tf:
		if line[0] == '#':
			continue
		m = re.match(tp.ftrace_line_fmt, line.strip())
		if(not m):
			continue
		m_time, m_proc, m_pid, m_msg, m_dur = \
			m.group('time', 'proc', 'pid', 'msg', 'dur')
		if float(m_time) > data.end:
			break
		if(m_time and m_pid and m_msg):
			t = aslib.FTraceLine(m_time, m_msg, m_dur)
			pid = int(m_pid)
		else:
			continue
		if t.fevent or t.fkprobe:
			continue
		key = (m_proc, pid)
		if(key not in ftemp):
			ftemp[key] = []
			ftemp[key].append(aslib.FTraceCallGraph(pid))
		cg = ftemp[key][-1]
		if(cg.addLine(t)):
			ftemp[key].append(aslib.FTraceCallGraph(pid))
	tf.close()

	# add the callgraph data to the device hierarchy
	for key in ftemp:
		proc, pid = key
		for cg in ftemp[key]:
			if len(cg.list) < 1 or cg.invalid:
				continue
			if(not cg.postProcess()):
				print('Sanity check failed for %s-%d' % (proc, pid))
				continue
			# match cg data to devices
			if not data.deviceMatch(cg):
				print ' BAD: %s %s-%d [%f - %f]' % (cg.list[0].name, proc, pid, cg.start, cg.end)

# Function: colorForName
# Description:
#	 Generate a repeatable color from a list for a given name
def colorForName(name, list):
	i = 0
	total = 0
	count = len(list)
	while i < len(name):
		total += ord(name[i])
		i += 1
	return list[total % count]

# Function: createBootGraph
# Description:
#	 Create the output html file from the resident test data
# Arguments:
#	 testruns: array of Data objects from parseKernelLog or parseTraceLog
# Output:
#	 True if the html file was created, false if it failed
def createBootGraph(data, embedded):
	# html function templates
	headline_version = '<div class="version">AnalyzeBoot v%s</div>' % sysvals.version
	headline_stamp = '<div class="stamp">{0} {1} {2} {3}</div>\n'
	html_device = '<div id="{0}" title="{1}" class="thread{7}" style="left:{2}%;top:{3}px;height:{4}px;width:{5}%;">{6}</div>\n'
	html_tblock = '<div id="block{0}" class="tblock" style="left:{1}%;width:{2}%;"><div class="tback" style="height:{3}px"></div>\n'
	html_phase = '<div class="phase" style="left:{0}%;width:{1}%;top:{2}px;height:{3}px;background-color:{4}">{5}</div>\n'
	html_phaselet = '<div id="{0}" class="phaselet" style="left:{1}%;width:{2}%;background-color:{3}"></div>\n'
	html_timetotal = '<table class="time1">\n<tr>'\
		'<td class="blue">Time from Kernel Boot to start of User Mode: <b>{0} ms</b></td>'\
		'</tr>\n</table>\n'

	# device timeline
	devtl = aslib.Timeline(100, 20)

	# Generate the header for this timeline
	t0 = data.start
	tMax = data.end
	tTotal = tMax - t0
	if(tTotal == 0):
		print('ERROR: No timeline data')
		return False
	boot_time = '%.0f'%(tTotal*1000)
	devtl.html['timeline'] += html_timetotal.format(boot_time)

	# determine the maximum number of rows we need to draw
	phase = 'boot'
	list = data.dmesg[phase]['list']
	devlist = []
	for devname in list:
		d = aslib.DevItem(0, phase, list[devname])
		devlist.append(d)
	devtl.getPhaseRows(devlist)
	devtl.calcTotalRows()

	# draw the timeline background
	devtl.createZoomBox()
	boot = data.dmesg[phase]
	length = boot['end']-boot['start']
	left = '%.3f' % (((boot['start']-t0)*100.0)/tTotal)
	width = '%.3f' % ((length*100.0)/tTotal)
	devtl.html['timeline'] += html_tblock.format(phase, left, width, devtl.scaleH)
	devtl.html['timeline'] += html_phase.format('0', '100', \
		'%.3f'%devtl.scaleH, '%.3f'%devtl.bodyH, \
		'white', '')

	# draw the device timeline
	color = ['c1', 'c2', 'c3', 'c4', 'c5',
		'c6', 'c7', 'c8', 'c9', 'c10']
	for d in list:
		name = d
		c = colorForName(name, color)
		dev = list[d]
		height = devtl.phaseRowHeight(0, phase, dev['row'])
		top = '%.3f' % ((dev['row']*height) + devtl.scaleH)
		left = '%.3f' % (((dev['start']-t0)*100)/tTotal)
		width = '%.3f' % (((dev['end']-dev['start'])*100)/tTotal)
		length = ' (%0.3f ms) ' % ((dev['end']-dev['start'])*1000)
		devtl.html['timeline'] += html_device.format(dev['id'], \
			d+length+'kernel_mode', left, top, '%.3f'%height, width, name, ' '+c)

	# draw the time scale, try to make the number of labels readable
	devtl.createTimeScale(t0, tMax, tTotal, phase)
	devtl.html['timeline'] += '</div>\n'

	# timeline is finished
	devtl.html['timeline'] += '</div>\n</div>\n'

	if(sysvals.outfile == sysvals.htmlfile):
		hf = open(sysvals.htmlfile, 'a')
	else:
		hf = open(sysvals.htmlfile, 'w')

	# no header or css if its embedded
	if(not embedded):
		aslib.addCSS(hf, 'Boot Graph')

	# write the test title and general info header
	if(data.stamp['time'] != ""):
		hf.write(headline_version)
		if sysvals.addlogs:
			hf.write('<button id="showdmesg" class="logbtn">dmesg</button>')
		hf.write(headline_stamp.format(data.stamp['host'],
			data.stamp['kernel'], 'boot', \
				data.stamp['time']))

	# write the device timeline
	hf.write(devtl.html['timeline'])

	# draw the colored boxes for the device detail section
	hf.write('<div id="devicedetailtitle"></div>\n')
	hf.write('<div id="devicedetail" style="display:none;">\n')
	hf.write('<div id="devicedetail%d">\n' % data.testnumber)
	hf.write(html_phaselet.format('kernel_mode', '0', '100', '#DDDDDD'))
	hf.write('</div>\n')
	hf.write('</div>\n')

	if(sysvals.usecallgraph):
		aslib.callgraphHTML(hf, data)

	# add the dmesg log as a hidden div
	if sysvals.addlogs:
		hf.write('<div id="dmesglog" style="display:none;">\n')
		for line in data.dmesgtext:
			line = line.replace('<', '&lt').replace('>', '&gt')
			hf.write(line)
		hf.write('</div>\n')

	if(not embedded):
		# write the footer and close
		aslib.addScriptCode(hf, [data])
		hf.write('</body>\n</html>\n')
	else:
		# embedded out will be loaded in a page, skip the js
		hf.write('<div id=bounds style=display:none>%f,%f</div>' % \
			(data.start*1000, data.initstart*1000))
	hf.close()
	return True

# Function: doError
# Description:
#	 generic error function for catastrphic failures
# Arguments:
#	 msg: the error message to print
#	 help: True if printHelp should be called after, False otherwise
def doError(msg, help=False):
	if(help == True):
		printHelp()
	print('ERROR: %s\n') % msg
	sys.exit()

# Function: printHelp
# Description:
#	 print out the help text
def printHelp():
	print('')
	print('AnalyzeBoot v%.1f' % sysvals.version)
	print('Usage: analyze_boot.py <options>')
	print('')
	print('Description:')
	print('  This tool reads in a dmesg log of linux kernel boot and')
	print('  creates an html representation of the boot timeline up to')
	print('  the start of the init process.')
	print('  If no arguments are given the tool reads the host dmesg log')
	print('  and outputs bootgraph.html')
	print('')
	print('Options:')
	print('  -h            Print this help text')
	print('  -v            Print the current tool version')
	print('  -dmesg file   Load a stored dmesg file')
	print('  -html file    Html timeline name (default: bootgraph.html)')
	print('  -addlogs      Add the dmesg log to the html output')
	print(' [advanced]')
	print('  -f            Use ftrace to add function detail (default: disabled)')
	print('  -ftrace file  Load a stored ftrace file')
	print('  -mincg  ms    Discard all callgraphs shorter than ms milliseconds (e.g. 0.001 for us)')
	print('')
	return True

# ----------------- MAIN --------------------
# exec start (skipped if script is loaded as library)
if __name__ == '__main__':
	# loop through the command line arguments
	args = iter(sys.argv[1:])
	for arg in args:
		if(arg == '-h'):
			printHelp()
			sys.exit()
		elif(arg == '-v'):
			print("Version %.1f" % sysvals.version)
			sys.exit()
		elif(arg == '-f'):
			sysvals.usecallgraph = True
		elif(arg == '-mincg'):
			aslib.sysvals.mincglen = aslib.getArgFloat('-mincg', args, 0.0, 10000.0)
		elif(arg == '-ftrace'):
			try:
				val = args.next()
			except:
				doError('No ftrace file supplied', True)
			if(os.path.exists(val) == False):
				doError('%s doesnt exist' % val)
			sysvals.ftracefile = val
		elif(arg == '-addlogs'):
			sysvals.addlogs = True
		elif(arg == '-dmesg'):
			try:
				val = args.next()
			except:
				doError('No dmesg file supplied', True)
			if(os.path.exists(val) == False):
				doError('%s doesnt exist' % val)
			if(sysvals.htmlfile == val or sysvals.outfile == val):
				doError('Output filename collision')
			sysvals.dmesgfile = val
		elif(arg == '-html'):
			try:
				val = args.next()
			except:
				doError('No HTML filename supplied', True)
			if(sysvals.dmesgfile == val):
				doError('Output filename collision')
			sysvals.htmlfile = val
		else:
			doError('Invalid argument: '+arg, True)

	data = loadKernelLog()
	if sysvals.usecallgraph:
		loadTraceLog(data)

	if(sysvals.outfile and sysvals.phoronix):
		fp = open(sysvals.outfile, 'w')
		fp.write('pass %s initstart %.3f end %.3f boot %s\n' %
			(data.valid, data.initstart*1000, data.end*1000, data.boottime))
		fp.close()
	if(not data.valid):
		if sysvals.dmesgfile:
			doError('No initcall data found in %s' % sysvals.dmesgfile)
		else:
			doError('No initcall data found, is initcall_debug enabled?')

	print('          Host: %s' % sysvals.hostname)
	print('     Test time: %s' % sysvals.testtime)
	print('     Boot time: %s' % data.boottime)
	print('Kernel Version: %s' % sysvals.kernel)
	print('  Kernel start: %.3f' % (data.start * 1000))
	print('    init start: %.3f' % (data.initstart * 1000))

	createBootGraph(data, sysvals.phoronix)
