#!/usr/bin/python3
# Copyright 2018  Simon Arlott
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.


import argparse
import collections
import csv
import enum
import pytz
import re
import sys


AvailablePeriod = collections.namedtuple('AvailablePeriod', ['time_zone', 'day', 'time_from', 'time_to', 'available'])

re_time = re.compile(r"([0-1][0-9]|2[0-3]):([0-5][0-9])")

class Availability(enum.Enum):
	No = 1
	Maybe = 2
	Yes = 3

players_required = 4


class PlayerAvailability:
	def __init__(self):
		self.times = set()

	def add(self, time_zone, day, time_from, time_to, available):
		self.times.add(AvailablePeriod(time_zone, day, time_from, time_to, available))

	def available_at(self, ts):
		# TODO
		return Availability.No


class TeamAvailability:
	def __init__(self):
		self.players = collections.defaultdict(PlayerAvailability)

	def add(self, player, time_zone, day, time_from, time_to, available):
		self.players[player].add(time_zone, day, time_from, time_to, available)

	def available_at(self, ts):
		available = collections.Counter(Availability.__members__.values())
		for player in self.players.values():
			player_availability = player.available_at(ts)
			# Count them in all availability levels up to the best one they have
			# (i.e. if they're a Yes, also count them as a Maybe)
			for availability in Availability.__members__.values():
				if availability.value <= player_availability.value:
					available[availability] += 1

		# Find the best availability status that has the required number of players
		result = (Availability.No, 0)
		for availability in Availability.__members__.values():
			if available[availability] >= players_required:
				result = (availability, available[availability])
		return result


class LeagueAvailability:
	def __init__(self, filename):
		self.teams = collections.defaultdict(TeamAvailability)

		with open(filename) as csvfile:
			header = False
			line = 0

			(last_team, last_player, last_time_zone, last_day, last_available) = 5*[""]

			for row in csv.reader(csvfile):
				line += 1
				if row == ["Team", "Player", "Time Zone", "Day", "From", "To", "Available"]:
					header = True
				elif header:
					(team, player, time_zone, day, time_from, time_to, available) = row

					if team == "":
						team = last_team
					if player == "":
						player = last_player
					if time_zone == "":
						time_zone = last_time_zone
					if day == "":
						day = last_day
					if available == "":
						available = last_available

					time_zone = time_zone.replace(" ", "_")

					if not team:
						raise Exception(f"Invalid team on line {line}")
					if not player:
						raise Exception(f"Invalid player on line {line}")
					if time_zone not in pytz.all_timezones_set:
						raise Exception(f"Invalid time zone on line {line}: {time_zone}")
					if day not in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
						raise Exception(f"Invalid day on line {line}: {day}")

					(last_team, last_player, last_time_zone, last_day, last_available) = (team, player, time_zone, day, available)

					match = re_time.fullmatch(time_from)
					if not match:
						raise Exception(f"Invalid from time on line {line}: {time_from}")
					time_from = (int(match.group(1)), int(match.group(2)))

					match = re_time.fullmatch(time_to)
					if not match:
						raise Exception(f"Invalid from time on line {line}: {time_to}")
					time_to = (int(match.group(1)), int(match.group(2)))
					if time_to == (00, 00):
						time_to = (24, 00)

					if available not in Availability.__members__.keys():
						raise Exception(f"Invalid availability on line {line}: {available}")
					available = Availability.__members__[available]

					self.teams[team].add(player, time_zone, day, time_from, time_to, available)

			if not header:
				raise Exception("Unable to find header")

	def available_at(self, ts):
		teams = {}

		for name, team in self.teams.items():
			(availability, players) = team.available_at(ts)
			if availability.value > Availability.No.value:
				teams[name] = (players, availability)

		return teams


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Process availability")
	parser.add_argument("filename", metavar="FILENAME", type=str, help="CSV file containing availability data")
	args = parser.parse_args()

	league = LeagueAvailability(args.filename)

	for team_name, team in league.teams.items():
		team.available_at(None)
		print(team_name)
		for player_name, player in team.players.items():
			print(player_name)
			for time in player.times:
				print(time)

	# TODO
