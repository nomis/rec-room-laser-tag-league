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


from datetime import datetime, timedelta, timezone
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

weekdays = dict(zip(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], range(1, 8)))
weekdays_inv = {v: k for k, v in weekdays.items()}


"""Cache time zone conversions for a given datetime"""
class Timestamp:
	def __init__(self, dt):
		self.dt = dt
		self.dt_tz = {}

	def astimezone(self, timezone):
		if timezone.zone not in self.dt_tz:
			self.dt_tz[timezone.zone] = self.dt.astimezone(timezone)
		return self.dt_tz[timezone.zone]


class PlayerAvailability:
	def __init__(self):
		self.periods = set()

	def add(self, time_zone, day, time_from, time_to, available):
		self.periods.add(AvailablePeriod(pytz.timezone(time_zone), weekdays[day], time_from, time_to, available))

	def available_at(self, ts):
		available = Availability.No

		for period in self.periods:
			player_ts = ts.astimezone(period.time_zone)
			if player_ts.isoweekday() != period.day:
				continue
			if player_ts.hour < period.time_from[0]:
				continue
			if player_ts.hour > period.time_to[0]:
				continue
			if player_ts.hour == period.time_from[0] and player_ts.minute < period.time_from[1]:
				continue
			if player_ts.hour == period.time_to[0] and player_ts.minute >= period.time_to[1]:
				continue
			if period.available.value > available.value:
				available = period.available

		return available


class TeamAvailability:
	def __init__(self):
		self.players = collections.defaultdict(PlayerAvailability)

	def add(self, player, time_zone, day, time_from, time_to, available):
		self.players[player].add(time_zone, day, time_from, time_to, available)

	def available_at(self, ts, players_required):
		available = collections.Counter()
		for player in self.players.values():
			player_availability = player.available_at(ts)
			# Count them in all availability levels up to the best one they have
			# (i.e. if they're a Yes, also count them as a Maybe)
			for availability in Availability.__members__.values():
				if availability.value <= player_availability.value:
					available[availability] += 1

		# Find the best availability status that has the required number of players
		result = (0, Availability.No)
		for availability in Availability.__members__.values():
			if available[availability] >= players_required:
				result = (available[availability], availability)
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
					if day not in weekdays.keys():
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

	def available_at(self, ts, players_required):
		teams = {}

		for name, team in self.teams.items():
			(players, availability) = team.available_at(ts, players_required)
			if availability.value > Availability.No.value:
				teams[name] = (players, availability)

		return teams


def generate_output(args, output=sys.stdout, time_zones={
			"WET/WEST (Western Europe)": (pytz.timezone("Europe/London"), "%d/%m"),
			"CET/CEST (Central Europe)": (pytz.timezone("Europe/Paris"), "%d/%m"),
			"Pacific (US)": (pytz.timezone("US/Pacific"), "%m/%d"),
			"Mountain (US)": (pytz.timezone("US/Mountain"), "%m/%d"),
			"Central (US)": (pytz.timezone("US/Central"), "%m/%d"),
			"Eastern (US)": (pytz.timezone("US/Eastern"), "%m/%d"),
		}):
	league = LeagueAvailability(args.filename)

	now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
	end = now + timedelta(weeks=args.weeks)
	dt = now

	last_teams = {}
	team_player_minimums = {}
	team_player_maximums = {}
	last_from = None

	rows = list(time_zones.keys()) + list(sorted(league.teams.keys()))
	csvfile = csv.DictWriter(output, rows, quoting=csv.QUOTE_ALL)
	csvfile.writeheader()

	while dt < end:
		teams = league.available_at(Timestamp(dt), args.players)
		if __summarise_teams(teams) != __summarise_teams(last_teams):
			if last_teams:
				__output_team_list(csvfile, time_zones, last_from, dt, last_teams, team_player_minimums, team_player_maximums)
			last_from = dt
			team_player_minimums = {}
			team_player_maximums = {}

		if teams and not last_teams:
			last_from = dt
			team_player_minimums = {}
			team_player_maximums = {}
		last_teams = teams
		for (team, (players, availability)) in teams.items():
			if team_player_minimums.get(team, sys.maxsize) > players:
				team_player_minimums[team] = players
			if team_player_maximums.get(team, 0) < players:
				team_player_maximums[team] = players

		dt += timedelta(minutes=1)

		if dt == end:
			if last_teams:
				__output_team_list(csvfile, time_zones, last_from, dt, last_teams, team_player_minimums, team_player_maximums)


def __summarise_teams(teams):
	return set([(team, availability) for (team, (players, availability)) in teams.items()])


def __output_team_list(csvfile, time_zones, dt_from, dt_to, teams, team_player_minimums, team_player_maximums):
	row = {}

	for (zone_name, (time_zone, date_format)) in time_zones.items():
		dt_from_tz = dt_from.astimezone(time_zone)
		dt_to_tz = dt_to.astimezone(time_zone)

		from_day = weekdays_inv[dt_from_tz.isoweekday()][0:2]
		to_day = weekdays_inv[dt_to_tz.isoweekday()][0:2]

		from_str = from_day + dt_from_tz.strftime(f" {date_format} %H:%M")
		if dt_from_tz.date() == dt_to_tz.date():
			to_str = dt_to_tz.strftime(f"%H:%M")
		else:
			to_str = to_day + dt_to_tz.strftime(f" {date_format} %H:%M")

		row[zone_name] = f"{from_str} to {to_str}"

	for (team, (players, availability)) in teams.items():
		if team_player_minimums[team] == team_player_maximums[team]:
			team_str = f"{players}"
		else:
			team_str = f"{team_player_minimums[team]}-{team_player_maximums[team]}"

		if availability == availability.Yes:
			row[team] = f"'{team_str}"
		else:
			row[team] = f"'({team_str})"

	csvfile.writerow(row)


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Process availability")
	parser.add_argument("filename", metavar="FILENAME", type=str, help="CSV file containing availability data")
	parser.add_argument("-w", "--weeks", type=int, default=4, help="Number of weeks to output")
	parser.add_argument("-p", "--players", type=int, default=4, help="Minimum number of players from each team")
	args = parser.parse_args()
	generate_output(args)
