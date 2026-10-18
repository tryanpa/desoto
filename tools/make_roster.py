"""Generate a random test roster: python tools/make_roster.py [periods] [per_period] > roster.json"""
import json
import random
import sys

FIRST = """Aaliyah Aiden Alex Amara Andre Aria Asher Ava Bella Beck Caleb Camila Carlos Chloe Dante Daniela Eli Elena
Emma Ethan Fatima Finn Gabriel Grace Hana Hector Iris Isaac Jada Jamal Jasmine Jayden Kai Keira Leah Liam Lucia
Malik Maya Mateo Mia Nadia Noah Nora Olivia Omar Priya Quinn Rafael Riley Rosa Sam Sofia Talia Tariq Uma Vivian
Wyatt Xavier Yara Zane Zoe""".split()
LAST = """Alvarez Bennett Brooks Carter Chen Contreras Crane Diaz Esqueda Flores Garcia Gomez Hidalgo Huang Ibrahim
Jackson Kim Lehr Lopez Martinez Mitchell Nguyen Okafor Patel Perez Quintero Ramirez Reyes Rivera Santos Singh
Tanaka Thompson Torres Vargas Walker Wilson Xiong Young Zhang""".split()

periods = int(sys.argv[1]) if len(sys.argv) > 1 else 6
per = int(sys.argv[2]) if len(sys.argv) > 2 else 28
rng = random.Random(42)
names = set()
roster = {}
for p in range(1, periods + 1):
    group = []
    while len(group) < per:
        n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if n not in names:
            names.add(n)
            group.append(n)
    roster[str(p)] = sorted(group, key=lambda n: n.split()[-1])
print(json.dumps(roster, indent=1))
