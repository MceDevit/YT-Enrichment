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

<!--
Two more optional section headings, left out here so the built-in defaults
stay active — add them yourself (as real "## " headings, same style as the
topic sections above) if you want to override the editorial instructions
used for every summary (separate from the per-topic `focus` above, which
only adds extra guidance on top):

  - a heading named "Summary Prompt" — its full body replaces the built-in
    editorial instructions for regular (non-Short) videos: bullet count,
    prioritization, verdict criteria, tone, etc.
  - a heading named "Short Summary Prompt" — same idea, for YouTube Shorts.
    No verdict is ever requested for Shorts regardless of what you put here.

Whatever text goes directly under either heading becomes the entire
override (not appended to the default — replaces it), so keep it complete.
-->
