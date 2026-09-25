# Claude Code Project Guide: CF Discord Bot

## Project Overview
A Discord bot that integrates with the Codeforces API to facilitate a competitive programming environment. It supports user registration, random problem practice with tracking, 1v1 Duels, and Group Contests.

## Core Architecture
- **Tech Stack**: Python, `discord.py`, `motor` (Async MongoDB), `aiohttp`, `apscheduler`.
- **DB Strategy**: "ID-Only" storage. Static data is fetched from CF API; only IDs and timestamps are stored in MongoDB to optimize for the Atlas Free Tier.
- ** State Management**: 
    - `active_sessions`: Ephemeral (deleted on solve/expiry).
    - `contests`: Ephemeral blueprint (deleted on finish).
    - `user_history` & `contest_history`: Persistent archives.

## Key Commands
- `/register <handle>`: Links Discord ID to CF handle.
- `/profile`: Fetches live CF rating/rank.
- `/randomquestion`: Assigns a problem and starts a 1-hour tracking session.
- `/check`: Manually triggers a solve check.
- `/skip`: Abandons current problem.
- `/challenge`: Initiates a 1v1 duel.
- `/createcontest`: Creates a group contest.
- `/joincontest`: Joins a waiting contest.
- `/startcontest`: Admin-only command to begin a contest.

## Critical Files
- `bot.py`: Main application logic and API integration.
- `.env`: Secrets (Token, Mongo URI).
- `requirements.txt`: Dependencies.
- `DESIGN.md`: Architectural deep dive.
- `README.md`: Setup and feature guide.
