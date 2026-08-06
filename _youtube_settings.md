use_claude: yes
max_transcript_minutes: 60
transcript_retries: 0
model_summary: claude-haiku-4-5-20251001    # used for the summary + verdict
model_reformat: claude-haiku-4-5-20251001   # used for transcript cleanup

## Default
focus: General summary — technical takeaways, actionable steps I could apply, any resources/tools mentioned by name, and a final verdict on whether it's worth watching in full.

## AI Technical How-To
keywords: ai, claude, chatgpt, gpt, llm, prompt, prompting, agent, copilot, mcp, model context protocol, automation, ai tool, ai workflow, vibe coding, cursor, midjourney, ai app
focus: Focus on the exact technique or workflow being taught — specific prompts, tools, or steps I could copy directly. Name any AI models, apps, or platforms mentioned. Flag if it's mostly hype/opinion versus an actual reproducible how-to.

## IBM i / Development
keywords: rpg, ibm i, sql, python, swift, xcode, app store, coding, developer, api, github, homebrew, javascript
focus: Focus on concrete code patterns, dev tools, and anything directly applicable to my IBM i/RPG or app development work. Call out any commands, libraries, or config steps by name.

## Guitar / Music
keywords: guitar, jazz, tarrega, manouche, baden powell, gypsy jazz, music, chord, scale, fretboard
focus: Focus on technique, repertoire, and practice tips rather than general commentary. Note any specific pieces, chord voicings, or exercises mentioned.

## Summary Prompt
Prioritize, in this order of importance:
1. Technical takeaways — tools, techniques, code patterns, or concrete methods shown.
2. Actionable steps I could apply myself.
3. Any specific links, tools, libraries, or resources mentioned by name.

End with one final bullet: a one-line verdict on whether it's worth watching in full — judged against my stated interest if I gave one, otherwise judge generally — and why or why not.

Be decisive. Default to Yes or No — only use Maybe if the video is a genuine toss-up (e.g. good content but a format you may not enjoy). Don't use Maybe just to hedge. If the video is mostly filler, hype, opinion without substance, or this summary already captures everything of value so the full video adds little, say No plainly and say why. Be critical — most videos are not worth watching in full even if they're fine to summarize.

Keep each bullet tight — one line where possible. No preamble, no restating the title. Don't describe the summary's position in the note (e.g. 'above' or 'below') — refer to it only as 'this summary'.

## Short Summary Prompt
No verdict — don't judge whether it's worth watching, Shorts are short enough already. Keep each bullet tight — one line where possible. No preamble, no restating the title.
