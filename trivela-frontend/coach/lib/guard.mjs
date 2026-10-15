/* Code-side guards (hard rules 6, 7, 10, 12): run before any model call, so a blocked question costs nothing.
   The system prompt repeats these rules; this layer is what a clever prompt cannot talk around. */

export const LIMITS = {
  questionChars: 600,      // longer questions are rejected, not trimmed
  questionsPerChat: 20,
  historyMessages: 6,      // the last 3 question/answer pairs go to the model
  toolCalls: 4,            // per chat answer, so the model cannot loop
  maxTips: 3,
  perMinute: 5,            // requests per visitor
  dailyQuestions: 30,      // chat questions per visitor per day
  dailyGenerations: 3,     // report / scouting / commentary / tips regenerations per visitor per day (cached reads are free)
  dailyFresh: 20,          // first-time generations per visitor per day: anyone can create matches, so this is capped too
  dailyVisitorTokens: 300000, // tokens per visitor per day, whatever they spend them on
  promptChars: 40000,      // a request bigger than this is refused: real matches are far smaller
};

/* Checked in this order; the first match wins. Messages follow the refusal style: why, in one line, then what we can do */
const BLOCKS = [
  { code: 'injection', message: "I can't change how I work. Ask me anything about this match's stats.",
    re: /\b(ignore|forget|disregard|override)\b.{0,30}\b(instructions?|rules?|prompt|guidelines)\b|\bsystem prompt\b|\byour (instructions|rules|prompt)\b|\b(you are now|pretend (to be|you are)|roleplay|role-play|jailbreak|developer mode|DAN mode)\b/i },
  { code: 'betting', message: "I don't do betting or odds. I can tell you what the numbers say about this match.",
    re: /\b(bet|bets|betting|bettor|odds|bookies?|bookmakers?|accumulators?|accas?|parlays?|wagers?|tipsters?|punters?|moneyline|over\/under|fantasy (football|team|points))\b/i },
  { code: 'prediction', message: "I can't predict results. I can show you what this match's numbers say.",
    re: /\b(who|which (team|side))\b.{0,25}\b(will|gonna|going to)\b.{0,15}\bwin\b|\bpredict(ion|ions|ed)?\b|\b(next|upcoming) (match|game)('s)? (score|result)\b/i },
  { code: 'off_topic', message: "I only answer questions about this match's stats. Try: who ran the most?",
    re: /```|\b(python|javascript|typescript|sql|html|css|php|c\+\+|regex|leetcode|linked list|source code|homework|recipe)\b|\bwrite (me )?(a |an )?(script|program|code|essay|poem|story|email|letter|song|cover letter)\b|\btranslat(e|ion)\b/i },
];

/* -> { ok: true, text } or { ok: false, code, message } */
export function checkQuestion(raw) {
  const text = String(raw ?? '').trim();
  if (!text) return { ok: false, code: 'empty', message: 'Ask me something about this match.' };
  if (text.length > LIMITS.questionChars) return { ok: false, code: 'too_long', message: `Keep questions under ${LIMITS.questionChars} characters.` };
  const hit = BLOCKS.find(b => b.re.test(text));
  return hit ? { ok: false, code: hit.code, message: hit.message } : { ok: true, text };
}

/* Hard rule 10: every tip must cite at least one number from the match, and there are at most 3 */
export const keepTipsWithNumbers = tips => (Array.isArray(tips) ? tips : []).filter(t => /\d/.test(String(t))).slice(0, LIMITS.maxTips);
