# Handoff Document: CF Discord Bot

## Current State
The bot is fully feature-complete. All core modules (Registration, Practice, Competitive Engine, and Automation) are implemented and pushed to GitHub. The database architecture is optimized for MongoDB Atlas Free Tier.

## Technical Debt & Open Items
- **Channel Hardcoding**: The `daily_question_job` and `contest_notification_job` currently look for a channel named `'general'`. This should be moved to a configuration setting in the database.
- **Rating Range**: The daily question is hardcoded to 800-1600. This could be made configurable.
- **Contest Results**: The winner is currently decided by most problems solved. A more complex "penalty time" system (standard CF rules) could be implemented.

## How to Resume Work
1. **Environment**: Ensure you are in the `venv` and `.env` is configured.
2. **API**: If updating the CF API logic, ensure `fetch_cf_api` remains asynchronous.
3. **Database**: When adding new features, check if they belong in the `active` (ephemeral) or `history` (persistent) collections to maintain the lean storage strategy.
4. **Testing**: Use a test CF account to verify submission detection before deploying to a large server.

## Next Potential Features
- Implement a global leaderboard for the most problems solved through the bot.
- Add "Duel History" lookup command (e.g., `/duelstats @user`).
- Integration with a specific CF Group to track internal group contests.
