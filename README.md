# Codeforces Discord Bot

A competitive programming bot that integrates Codeforces API with Discord to facilitate practice, 1v1 Duels, and Group Contests.

## 🚀 Features
- **User Registration**: Link Discord accounts to Codeforces handles.
- **Profile Tracking**: Live fetching of CF ratings and ranks.
- **Random Practice**: Get a random problem within a rating range and have the bot track your submission status automatically.
- **1v1 Duels**: Challenge a friend to a timed competition with automatically selected problems.
- **Group Contests**: Create a contest, invite participants, and start it manually as an admin.
- **Live Polling**: Background tracking of submissions to announce wins in real-time.
- **Submission History**: Lean storage of solved problems.

## 🛠️ System Flow

### Registration & Profile
```mermaid
graph TD
    A[User: /register handle] --> B{CF API: Valid?}
    B -- No --> C[Error: Handle not found]
    B -- Yes --> D[Store DiscordID -> CFHandle in DB]
    D --> E[Success Message]
    
    F[User: /profile] --> G[Lookup handle in DB]
    G --> H{Handle found?}
    H -- No --> I[Error: Register first]
    H -- Yes --> J[Fetch live stats from CF API]
    J --> K[Display Embed with Rating/Rank]
```

### Practice Tracking
```mermaid
graph TD
    A[User: /randomquestion] --> B[Fetch Problem Set]
    B --> C[Filter by Rating & Pick Random]
    C --> D[Create Pending Session in DB]
    D --> E[Send Problem Link]
    
    F[Background Poller] --> G{Session Pending?}
    G -- Yes --> H{Expired > 1hr?}
    H -- Yes --> I[Delete Session]
    H -- No --> J[Fetch user.status from CF API]
    J --> K{Verdict == 'OK'?}
    K -- Yes --> L[Archive to User History]
    L --> M[Delete Active Session]
    M --> N[Post Victory Message]
    K -- No --> O[Wait for next poll]
```

### Duels & Contests
```mermaid
graph TD
    A[Creator: /challenge or /createcontest] --> B[Select Problems & Set Time]
    B --> C[Status: WAITING]
    C --> D[Participants Join via /joincontest]
    D --> E[Admin: /startcontest]
    E --> F[Status: ACTIVE]
    F --> G[Reveal Problems to all]
    G --> H[Poller tracks solve progress]
    H --> I{Timer Expired or All Solved?}
    I -- Yes --> J[Calculate Winner]
    J --> K[Archive to Contest History]
    K --> L[Delete Active Contest]
    L --> M[Post Final Leaderboard]
```

## 📦 Installation
1. Clone the repository.
2. Install Python 3.11+.
3. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate # Windows: venv\Scripts\activate
   ```
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Configure `.env` with `DISCORD_TOKEN` and `MONGO_URI`.
6. Run the bot:
   ```bash
   python bot.py
   ```
