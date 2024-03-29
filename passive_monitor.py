#!/usr/bin/env python
# -*- coding: utf-8 -*-

from threading import Thread
import subprocess as sub
import logging as log
import time
import collections
import sys
import os
import json

import simpleNamespace
import si_formatter
import argument_parse

output_file = 'pping/output.txt'
pping = None

class pping_monitor(Thread):

	def __init__(self,interface,logger, time_interval=1, output_file=output_file):
		Thread.__init__(self)
		self.interface = interface
		self.log = logger
		self.time_interval = time_interval

		self.data = collections.OrderedDict()
		self.exec_flag = False
		self.process = []
		self.last_data_send = 0
		self.output = output_file
		self.pping_output_file = output_file
		self.arq_output = None

	def start_monitor(self):
		arq_output = open(self.pping_output_file, 'w')
		pping_process = sub.Popen(['./pping/pping', '-m', '-i', self.interface, '-q'], stdout=open(self.output, 'w'))
		self.process.append(pping_process)

	def stop(self):
		self.stop_monitor()

	def stop_monitor(self):
		for p in self.process:
			log.info('Stopping child proccess: [%s]' % ( p.pid))
			child_process = ''
			if p is not None:
				child_process += ('' if len(child_process) == 0 else '\|') + str(p.pid)
				p.terminate()
			if p is not None:
				p.kill()

		self.stop_thread()

		if self.arq_output and not self.arq_output.closed:
			arq_output.close()

	def stop_thread(self):
		self.exec_flag = False


	def run(self):
		self.exec_flag = True

		if len(self.process) == 0:
			self.start_monitor()

		lines_read = 0

		while self.exec_flag:
			#  wait some data be collect without unnecessary tests
			last_metric = time.time()
			while (time.time() - last_metric) < self.time_interval:
				time.sleep(0.1)
			
			data, lines_read = self._get_data_from_file(self.output, lines_read)
			
			if len(data) != 0:
				struct_data = {}

				lines = data.split("\n")
				for line in lines:
					data_tupla = self._parser(line)

					if None not in data_tupla:
						self._added_raw_data(struct_data, data_tupla)
				
				fine_data = self._interprete_data(struct_data)
				
				for f in fine_data.keys():
					if f in self.data:
						self.data[f].update(fine_data[f])
					else:
						self.data[f] = fine_data[f]
				log.debug(self.data)
		

	def analyze_file(self, file):
		data, lines_read = self._get_data_from_file(file, 0)

		if len(data) != 0:
			struct_data = {}

			lines = data.split("\n")
			for line in lines:
				data_tupla = self._parser(line)

				if None not in data_tupla:
					self._added_raw_data(struct_data, data_tupla)
			
			fine_data = self._interprete_data(struct_data)
			
			for f in fine_data.keys():
				if f in self.data:
					self.data[f].update(fine_data[f])
				else:
					self.data[f] = fine_data[f]
			log.debug(self.data)


	def _unproccessed_data(self):
		data = collections.OrderedDict()
		tm = 0
		for tm in self.data.keys():
			if tm > self.last_data_send:
				data[tm] = self.data[tm]
		self.last_data_send = tm
		return data


	def _get_data_from_file(self, file=None, old_lines = 0):
		""" recupera as ultima linhas adicionadas do arquivo dado
		file: String, local e nome do arquivo onde esta os dados
		old_lines: Integer, devolve as ultimas linhas a partir dessas linhas
		"""
		if not file:
			file = self.output

		actual_lines = sub.check_output(['wc', '-l', file])
		actual_lines = int(actual_lines.split(' ')[0])

		txt = sub.check_output(['tail', '-n', str(actual_lines - old_lines), file])
		log.debug("actual_line: %s / new_lines(txt): %s" % (actual_lines, len(txt)))
		return (txt, actual_lines)

	def _parser(self, line):
		"""Faz parser de uma linha do texto e retorna uma tupla

		line: (String), from pping process output file
		return: (Tuple), (time, send_ip, destiny_ip, rtt_actual, rtt_minimum)
		"""

		"""
		One line has the fields:
		line: time, rtt, fr->min, fBytes, dBytes, pBytes, hosts#
		time: in epoch format
		fr->min: shortest round trip delay seen so far for this flow 
		fBytes: total bytes of flow through CP including this pkt
		dBytes: total bytes of in
		pBytes: 
		hosts: flow in the form:  srcIP:port+dstIP:port
		"""
		tm, rtt, fr_min = 0, 0, 0
		send, recv = '', ''

		line = line.split(" ")
		if len(line) < 6:
			log.debug("Linha pequena")
			return (None,) * 5

		try:
			tm = float(line[0])  # time when the packet was receive, in epoch
		except Exception as e:
			log.error(e)
			log.error('inside: %s' % line)
			return (None,) * 5

		rtt = float(line[1])  # the round-trip-time
		fr_min = float(line[2])  # minimun rtt

		try:
			hosts = line[-1].split("+")
			send = hosts[0].split(":")[0]  # send of the packet
			recv = hosts[1].split(":")[0]  # receiver of the packet
		except IndexError:
			log.error('IndexError')
			log.error('line: %s' % line)
			return (None,) * 5

		log.debug("[%f] %s -> %s _ %s [%s]" % (tm, send, recv, rtt, fr_min))
		return (tm, send, recv, rtt, fr_min)

	def _added_raw_data(self, struct_data, new_data):
		"""Added the new data to the structure to be processed later

		raw_data: (Structure ordended of Dicts): [ int(tm) ][ ip_sender ][ ip_receiver ][ rtt, rtt_minimum ]
		new_data: (Tuple)
		"""
		tm, send, recv, rtt, fr_min = new_data
		if tm is None:
			return
		if int(tm) not in struct_data:
			struct_data[int(tm)] = {}

		if send not in struct_data[int(tm)]:
			struct_data[int(tm)][send] = {}

		if recv not in struct_data[int(tm)][send]:
			struct_data[int(tm)][send][recv] = [[rtt, fr_min]]
		else:
			data = struct_data[int(tm)][send][recv]
			data.append( [rtt, fr_min] )

	def _interprete_data(self, struct_data):
		"""
		resume as informações contidas na estrutura de informações, transformando a lista de informações por segundo
		em rtt maior, menor e média, jtt maior, menor e média, e total de packets amostrados
		:param results: dict( <times> : dict ( <senders>: dict( <receivers>: list( [rtt, min->rtt]))))

		:return: dict( <times> : dict ( <senders>: dict( <receivers>: (maior, menor, media, total))))
		"""
		relatory = {}
		def traver_data(data):
			for tm in data:
				for send in data[tm]:
					receivers = data[tm][send]
					for recv in receivers:
						info = receivers[recv]
						#log.debug('returning: %s %s %s %s' % (tm, send, recv, info))
						yield tm, send, recv, info

		
		for tm, send, recv, infos in traver_data(struct_data):
			if time.time() - tm < 1:
				#  the program is still fetching one second of information about this period of time
				continue
			
			soma, total, media = 0, 0, 0
			max_rtt, min_rtt = 0, 0
			previous_rtt, jitter = None, 0
			min_jtt, max_jtt, sum_jtt = float('inf'), 0, 0

			if len(infos) > 0:
				max_rtt = infos[0][0]
				min_rtt = infos[0][0]

			for register in infos:
				actual_rtt = register[0]
				if actual_rtt > max_rtt:
					max_rtt = actual_rtt
				if actual_rtt < min_rtt:
					min_rtt = actual_rtt
				soma += actual_rtt
				total += 1

				# Jitter calculation
				if previous_rtt is None:
					previous_rtt = register[0]
				else:
					jitter = previous_rtt - actual_rtt
					if jitter < min_jtt:
						min_jtt = jitter
					if jitter > max_jtt:
						max_jtt = jitter
					sum_jtt += abs(jitter)

			if soma != 0 and total != 0:
				media = soma/total
			else:
				media = max_rtt			

			if total == 1:
				media_jtt = max_jtt
			else:
				media_jtt = sum_jtt / total

			infos = simpleNamespace.SimpleNamespace(total=total,
													max_rtt=max_rtt, 
													min_rtt=min_rtt, 
													media_rtt=media, 
													max_jtt=max_jtt, 
													min_jtt=min_jtt, 
													media_jtt=media_jtt)

			if tm not in relatory:
				relatory[tm] = {}

			if send not in relatory[tm]:
				relatory[tm][send] = {}

			header = '[%s] %s -> %s' % ( time.strftime('%H:%M:%S', time.gmtime(tm)), send, recv)
			log.info(header)
			log.info('rtt: max:%s / min:%s / media:%s / Samples: %s' %
				(si_formatter.format_float_string(max_rtt, 's'),
				si_formatter.format_float_string(min_rtt, 's'),
				si_formatter.format_float_string(media, 's'),
				total))

			log.info('jtt: max:%s / min %s / sum %s / media %s' %
				(	si_formatter.format_float_string(max_jtt, 's'),
					si_formatter.format_float_string(min_jtt, 's'),
					si_formatter.format_float_string(sum_jtt, 's'),
					si_formatter.format_float_string(sum_jtt / total, 's')))

			relatory[tm][send][recv] = infos
		return relatory

	def get_statistics(self, data=()):
		if len(data) == 0:
			return (0, 0, 0, 0)




def teste(args):
	global pping

	log.debug('Args %s' % args)

	log.info('Starting monitor')	
	pping = pping_monitor('enp3s0', log, output_file='run_teste.txt')

	if args.analyzer:
		log.info( json.dumps(pping.analyze_file(args.file), indent=4) )

	else:
		pping.start()

		init_time = time.time()
		line = 0

		while time.time() - init_time < 20:
			time.sleep(.5)
			data = pping._unproccessed_data()
			
		pping.stop_monitor()


if __name__ == '__main__':
	global pping

	args = argument_parse.set_argparse()
	format = '%(funcName)s [%(lineno)s]: %(msg)s'
	log.basicConfig(level=args.level, format=format)
		
	try:
		teste(args)
	except KeyboardInterrupt:
		log.info('Keyboard Interruption')
		pping.stop_monitor()

		try:
			sys.exit(0)
		except SystemExit:
			os._exit(0)
	except Exception as e:
		print(e)
		pping.stop_monitor()
		log.error('Error')
		raise e
