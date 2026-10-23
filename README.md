# waiting-on-you

How long did your AI agents sit there waiting for you last week?

I ran this on my own logs and the answer was about 187 hours. In one week. There are only 168 hours in a week, so yes, I checked the maths twice. It adds up because I had five sessions going at once and every one of them was patiently waiting for me to come back and say "yes, keep going".

So the slow part of my AI team was me. Fair enough.

## Run it

You need Python 3.10 or newer. Nothing to install.

```bash
curl -fsSLO https://raw.githubusercontent.com/yail259/waiting-on-you/main/waiting_on_you.py
python3 waiting_on_you.py
```

You get something like this:

```
Your agents waited on you for 186.8 hours.

  324 waits across 103 sessions, median 3.2 min.
  At the worst moment, 5 sessions were waiting on you at once.
  For 104.2 hours of the clock, at least one was.

  asked you a question: 42 times, 24.9 h
  finished, waiting for your next step: 282 times, 161.9 h

  2026-10-16  █████████████ 29.3 h
  2026-10-17  ████████████████████████ 55.5 h
  ...
```

Some flags:

```bash
python3 waiting_on_you.py --days 30          # a longer look back
python3 waiting_on_you.py --since 2026-10-16 --until 2026-10-23
python3 waiting_on_you.py --share            # one line to paste somewhere
python3 waiting_on_you.py --json             # the numbers, for your own charts
python3 waiting_on_you.py --only claude      # or --only codex
```

## What counts as waiting

A wait starts when the agent sends its last message in a turn and ends when you type your next prompt in that same session. The agent has stopped. Nothing happens until you show up.

- Replies within 20 seconds don't count. You were right there, that's just a conversation.
- Gaps over 8 hours don't count either. That's sleep, or a project you parked. They get reported separately.
- Subagents don't count, only the session you were actually talking to.
- If the agent's last words ended in a question mark, the wait is filed under "asked you a question". Otherwise it's "finished, waiting for your next step".

Parallel sessions add up, which is the whole point. Two sessions waiting for an hour each is two hours of agent time sitting idle, even though only one hour went by on the clock. The tool shows both numbers so you can see which one hurts more.

## Where it looks

- Claude Code: `~/.claude/projects`
- Codex: `~/.codex/sessions` (beta, I only had a handful of Codex sessions to test against)

On WSL it also finds the Windows copies under `/mnt/c/Users/*/`. Point it somewhere else with `--claude-dir` or `--codex-dir`.

## Privacy

It only reads. It looks at timestamps, and at whether the agent's last message ended in a question mark. It never prints, stores or sends any message text. Nothing leaves your machine. It's one file, so you can read the whole thing before you run it, which honestly you should do with anything you curl.

## Why I made this

I build [Restless](https://restless.run), an open-source office where agents sort out the routine between themselves and only bring you the calls that actually need you. Before building any more of it, I wanted to know how bad the problem really was for me. This is the script I wrote to find out. It turned out to be pretty bad.

If your number is worse than mine, I'd genuinely like to see it.

## License

Apache 2.0.
