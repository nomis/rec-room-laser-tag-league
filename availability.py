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


AvailablePeriod = collections.namedtuple('AvailablePeriod', ['time_zone', 'day', 'time_from', 'time_to', 'available', 'date_from', 'date_to'])

re_time = re.compile(r"([0-1][0-9]|2[0-3]):([0-5][0-9])")
re_date = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
re_username = re.compile(r"(?P<name>.+) (?P<username>\(@[^ ]+\))")

class Availability(enum.Enum):
	No = 1
	Maybe = 2
	Yes = 3

weekdays = dict(zip(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], range(1, 8)))
weekdays_inv = {v: k for k, v in weekdays.items()}
next_day = {}

for day in weekdays:
	next = weekdays[day] + 1
	if next > 7:
		next = 1
	next_day[day] = weekdays_inv[next]


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

	def add(self, time_zone, day, time_from, time_to, available, date_from, date_to):
		if time_to < time_from:
			self.periods.add(AvailablePeriod(pytz.timezone(time_zone), weekdays[day], time_from, (24, 00), available, date_from, date_to))
			self.periods.add(AvailablePeriod(pytz.timezone(time_zone), weekdays[next_day[day]], (00, 00), time_to, available, date_from, date_to))
		else:
			self.periods.add(AvailablePeriod(pytz.timezone(time_zone), weekdays[day], time_from, time_to, available, date_from, date_to))

	def available_at(self, ts):
		available = set()

		for period in self.periods:
			player_ts = ts.astimezone(period.time_zone)

			if period.date_from:
				if (player_ts.year, player_ts.month, player_ts.day) < period.date_from:
					continue
			if period.date_to:
				if (player_ts.year, player_ts.month, player_ts.day) > period.date_to:
					continue

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

			available.add(period.available)

		if Availability.No in available:
			return Availability.No
		elif Availability.Yes in available:
			return Availability.Yes
		elif Availability.Maybe in available:
			return Availability.Maybe
		else:
			return Availability.No


class TeamAvailability:
	def __init__(self):
		self.players = collections.defaultdict(PlayerAvailability)

	def add(self, player, *args):
		self.players[player].add(*args)

	def available_at(self, ts, players_required):
		available = collections.Counter()
		for name, player in self.players.items():
			player_availability = player.available_at(ts)
			multiplier = players_required if name == "*" else 1
			# Count them in all availability levels up to the best one they have
			# (i.e. if they're a Yes, also count them as a Maybe)
			for availability in Availability.__members__.values():
				if availability.value <= player_availability.value:
					available[availability] += multiplier

		# Find the best availability status that has the required number of players
		result = (0, Availability.No)
		for availability in Availability.__members__.values():
			if available[availability] >= players_required:
				result = (available[availability], availability)
		return result

	def any_available_at(self, ts, players_required):
		available = collections.Counter()
		for name, player in self.players.items():
			player_availability = player.available_at(ts)
			multiplier = players_required if name == "*" else 1
			available[player_availability.value] += multiplier

		total = available[Availability.Yes.value] + available[Availability.Maybe.value]
		if total == 0:
			return (0, Availability.No)

		if available[Availability.Yes.value] >= players_required or available[Availability.Maybe.value] == 0:
			return (total, Availability.Yes)
		return (total, Availability.Maybe)


class LeagueAvailability:
	def __init__(self, filename):
		self.teams = collections.defaultdict(TeamAvailability)

		with open(filename) as csvfile:
			header = False
			line = 0

			(last_team, last_player, last_time_zone, last_day, last_available) = 5*[""]

			for row in csv.reader(csvfile):
				line += 1
				if row == ["Team", "Player", "Time Zone", "Day", "From", "To", "Available", "Date From", "Date To"]:
					header = True
				elif header:
					(team, player, time_zone, day, time_from, time_to, available, date_from, date_to) = row

					if not list(filter(None, row)):
						# Skip blank rows
						continue

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

					(last_team, last_player, last_time_zone, last_day, last_available) = (team, player, time_zone, day, available)

					days = set()
					for weekday in day.split(","):
						if "-" in weekday:
							weekday = weekday.split("-")
							if len(weekday) != 2 or weekday[0] not in weekdays.keys() or weekday[1] not in weekdays.keys():
								raise Exception(f"Invalid day on line {line}: {day}")
							day = weekday[0]
							days.add(day)
							while day != weekday[1]:
								day = next_day[day]
								days.add(day)
						elif day not in weekdays.keys():
							raise Exception(f"Invalid day on line {line}: {day}")
						else:
							days.add(weekday)

					match = re_time.fullmatch(time_from)
					if not match:
						raise Exception(f"Invalid from time on line {line}: {time_from}")
					time_from = (int(match.group(1)), int(match.group(2)))

					match = re_time.fullmatch(time_to)
					if not match:
						raise Exception(f"Invalid to time on line {line}: {time_to}")
					time_to = (int(match.group(1)), int(match.group(2)))
					if time_to == (00, 00):
						time_to = (24, 00)

					if available not in Availability.__members__.keys():
						raise Exception(f"Invalid availability on line {line}: {available}")
					available = Availability.__members__[available]

					if date_from != "":
						match = re_date.fullmatch(date_from)
						if not match:
							raise Exception(f"Invalid from date on line {line}: {date_from}")
						date_from = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
					else:
						date_from = None

					if date_to != "":
						match = re_date.fullmatch(date_to)
						if not match:
							raise Exception(f"Invalid to date on line {line}: {date_to}")
						date_to = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
					else:
						date_to = None

					for day in days:
						self.teams[team].add(player, time_zone, day, time_from, time_to, available, date_from, date_to)

			if not header:
				raise Exception("Unable to find header")

		self.players = {}
		for name, team in self.teams.items():
			for name2, player in team.players.items():
				match = re_username.fullmatch(name2)
				if match:
					name2 = match.groupdict()["name"]
				self.players[f"{name} / {name2}"] = player

	def teams_available_at(self, ts, players_required):
		teams = {}

		for name, team in self.teams.items():
			(players, availability) = team.available_at(ts, players_required)
			if availability.value > Availability.No.value:
				teams[name] = (players, availability)

		return teams

	def teams_any_available_at(self, ts, players_required):
		teams = {}

		for name, team in self.teams.items():
			(players, availability) = team.any_available_at(ts, players_required)
			if availability.value > Availability.No.value:
				teams[name] = (players, availability)

		return teams

	def players_available_at(self, ts):
		players = set()

		for name, player in self.players.items():
			availability = player.available_at(ts)
			if availability.value > Availability.No.value:
				players.add((name, availability))

		return players


def generate_output(args, output=sys.stdout, time_zones={
			"WET/WEST (Western Europe)": (pytz.timezone("Europe/London"), "%d/%m", "%H:%M"),
			"CET/CEST (Central Europe)": (pytz.timezone("Europe/Paris"), "%d/%m", "%H:%M"),
			"Pacific (US)": (pytz.timezone("US/Pacific"), "%m/%d", "%I:%M %p"),
			"Mountain (US)": (pytz.timezone("US/Mountain"), "%m/%d", "%I:%M %p"),
			"Central (US)": (pytz.timezone("US/Central"), "%m/%d", "%I:%M %p"),
			"Eastern (US)": (pytz.timezone("US/Eastern"), "%m/%d", "%I:%M %p"),
		}):
	league = LeagueAvailability(args.filename)

	now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
	end = now + timedelta(weeks=args.weeks)
	dt = now

	if args.detail:
		last_players = set()
		last_from = None

		rows = list(time_zones.keys()) + list(sorted(list(league.teams.keys()) + list(league.players.keys())))
		csvfile = csv.DictWriter(output, rows, quoting=csv.QUOTE_ALL)
		csvfile.writeheader()

		while dt < end:
			players = league.players_available_at(Timestamp(dt))
			if players != last_players:
				if last_players:
					__output_player_list(csvfile, time_zones, last_from, dt, last_players,
						league.teams_any_available_at(Timestamp(last_from), args.players))
				last_from = dt

			if players and not last_players:
				last_from = dt
			last_players = players

			dt += timedelta(minutes=1)

			if dt == end:
				if last_players:
					__output_player_list(csvfile, time_zones, last_from, dt, last_players,
						league.teams_any_available_at(Timestamp(last_from), args.players))
	else:
		last_teams = {}
		team_player_minimums = {}
		team_player_maximums = {}
		last_from = None

		rows = list(time_zones.keys()) + list(sorted(league.teams.keys()))
		csvfile = csv.DictWriter(output, rows, quoting=csv.QUOTE_ALL)
		csvfile.writeheader()

		while dt < end:
			teams = league.teams_available_at(Timestamp(dt), args.players)
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


def __make_from_to(row, time_zones, dt_from, dt_to):
	for (zone_name, (time_zone, date_format, time_format)) in time_zones.items():
		dt_from_tz = dt_from.astimezone(time_zone)
		dt_to_tz = dt_to.astimezone(time_zone)

		from_day = weekdays_inv[dt_from_tz.isoweekday()][0:2]
		to_day = weekdays_inv[dt_to_tz.isoweekday()][0:2]

		from_str = from_day + dt_from_tz.strftime(f" {date_format} {time_format}")
		if dt_from_tz.date() == dt_to_tz.date():
			to_str = dt_to_tz.strftime(f"{time_format}")
		else:
			to_str = to_day + dt_to_tz.strftime(f" {date_format} {time_format}")

		row[zone_name] = f"{from_str} to {to_str}"


def __output_team_list(csvfile, time_zones, dt_from, dt_to, teams, team_player_minimums, team_player_maximums):
	row = {}

	__make_from_to(row, time_zones, dt_from, dt_to)

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


def __output_player_list(csvfile, time_zones, dt_from, dt_to, players, teams):
	row = {}

	__make_from_to(row, time_zones, dt_from, dt_to)

	for (player, availability) in players:
		if availability == availability.Yes:
			row[player] = f"X"
		else:
			row[player] = f"?"

	for (team, (players, availability)) in teams.items():
		if availability == availability.Yes:
			row[team] = f"'{players}"
		else:
			row[team] = f"'({players})"

	csvfile.writerow(row)


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Process availability")
	parser.add_argument("filename", metavar="FILENAME", type=str, help="CSV file containing availability data")
	parser.add_argument("-w", "--weeks", type=int, default=8, help="Number of weeks to output")
	parser.add_argument("-p", "--players", type=int, default=4, help="Minimum number of players from each team")
	parser.add_argument("-d", "--detail", action="store_true", help="Detailed player view (instead of per team)")
	args = parser.parse_args()
	generate_output(args)
