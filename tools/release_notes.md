Epic Seven bot for the official PC clients, STOVE or Steam. The zip holds one **E7Bot** folder that drives either. To run STOVE and Steam at the same time, copy the folder: each copy keeps its own settings and log.

## Getting started
1. Unzip anywhere except Program Files.
2. Start Epic Seven and log in. Leave its window open, not minimized.
   **The game must be in English** (Settings > Language): the bot reads English screens.
3. Run `E7Bot.exe`. On the first launch it asks which client to drive (in a copy of the folder, pick the other one).
   With **STOVE** the bot asks for administrator rights, because the STOVE game runs as administrator. Steam asks nothing.
4. In **Settings > Reroll**, pick **your** server and what makes an account worth keeping. The reset erases the account on that server, for good.
5. Pick a mode in the left bar, check its settings under "This run", then press **Start** at the top right.
   For a reroll, "Erase this account and start over…" erases the current account and starts again. It takes two clicks.
6. **Stop** stops the bot. While it runs you can look at the other modes: the running one stays marked in the bar.

## Good to know
- To reset, the bot types the account's nickname, and the game only takes the exact spelling. OCR sometimes mixes up o/0 or l/1, so it tries other spellings, about one every 10 seconds ("spelling 2 of 6"). That's expected.
- If you reset an account yourself, restart the bot with "Erase this account and start over…".
- The bot never takes your mouse or keyboard, and keeps the screen awake while it runs.
- The window uses Microsoft Edge WebView2, which Windows 11 already has. If it's missing, a message says so at launch.
- Windows Defender or SmartScreen may flag `E7Bot.exe`. It's a common false positive with packaged, unsigned Python programs.
