/* Football know-how for every stat in catalog.mjs, curated from the Trivela knowledge base
   (Desktop/Raw/Improved data). Each entry only reads stats Trivela can report.
   IDs in `source` point back to that knowledge base. Paraphrased, never quoted. */

export const PLAYBOOK = {
  possession: {
    read: 'More possession means more time on the ball, not a better performance. High possession with few strikes reads as sterile circulation; low possession with many balls won and strikes reads as a side that let the other team have it and hurt them on the turnover.',
    watch: 'Possession methods differ (time on the ball vs share of passes), so never compare our % with TV or other providers. Trivela takes it from the tracked ball, so low ball visibility makes it rough.',
    coach: 'Having the ball is only worth it if we do something with it.',
    source: 'I-013, I-024, CONFLICT-004',
  },
  passing: {
    read: 'Pass count shows who kept the ball moving. Compare it with strikes: lots of passes and few strikes means the passing did not lead anywhere. A side with fewer passes but similar strikes was more direct.',
    watch: 'We count completed passes between tracked players; a pass to a player off camera may be missed.',
    coach: 'Stop passing just to keep it; play with tempo and risk the forward ball.',
    source: 'I-013, I-014, P-026',
  },
  pass_accuracy: {
    read: 'Accuracy rewards safe passing. Very high accuracy with few strikes can mean the team played safe sideways and backwards balls. Lower accuracy with more strikes can mean braver, riskier passing.',
    watch: 'A low accuracy for one player can come from a few attempts; check how many passes it is based on before judging.',
    coach: 'Passing backwards is fine when it draws them out; it is a problem when it is all we do.',
    source: 'I-003, I-023',
  },
  balls_won: {
    read: 'Balls won counts how often a side took the ball off the other. A side with less possession but more balls won defended actively; many balls won by the team with the ball means they won it straight back after losing it.',
    watch: 'We see who gained the ball, not how (tackle, interception or loose ball). Never call them tackles or interceptions.',
    coach: 'Winning the ball is half the job; the next pass matters as much.',
    source: 'P-005, I-024',
  },
  strikes: {
    read: 'A strike is a hard kick of the ball the model detected, with the ball speed. It is the closest thing we have to attempts, so it is the best check on whether possession and passing led anywhere.',
    watch: 'A strike is not a shot on target and not a goal; a long clearance can count. Never turn strikes into shots, goals or xG.',
    coach: 'Count what our possession produced, not just how much of it we had.',
    source: 'Trivela engine definition',
  },
  distance: {
    read: 'More kilometres does not mean playing better. A side with low possession and high distance usually spent the game chasing the ball. A side with the ball can control the game and still run less.',
    watch: 'Camera tracking reads about 4% more distance than GPS vests, so never compare with GPS training numbers. Players on screen for different times must be compared per minute.',
    coach: 'Running more than them does not mean we played better; make the ball do the work.',
    source: 'I-024, I-009, CONFLICT-005, CONFLICT-006',
  },
  top_speed: {
    read: 'Top speed is the single fastest moment of a player or side. It shows who has pace, not who worked hardest.',
    watch: 'One fast moment can come from a tracking glitch; treat a top speed far above the rest of the squad with care. Camera smoothing can also shave a little off true peaks.',
    coach: 'Pace is a weapon only if we give it space to run into.',
    source: 'CONFLICT-005, G-004',
  },
  sprints: {
    read: 'Sprints are runs above 25 km/h. Many sprints point to an open, end-to-end game or a pressing side. Few sprints can simply mean there was no space to run into, for example against a deep defence, not that the players were unfit.',
    watch: 'Trivela uses 25 km/h; some studies use 24 km/h, which gives 25 to 40% more sprints, so never compare counts across sources. A fixed threshold undercounts effort from slower players such as centre-backs.',
    coach: 'You can be as fit as anyone and still have nowhere to sprint against a deep block.',
    source: 'CONFLICT-002, I-026, G-004',
  },
  touches: {
    read: 'Touches show involvement: how often a player was on the ball. A forward with few touches is not automatically lazy; some are told to stay high as the out-ball.',
    watch: 'Touches depend on time on screen; compare per minute between tracks.',
    coach: 'Stay high and give us the out-ball the moment we win it.',
    source: 'I-299',
  },
  final_third: {
    read: 'Time in the final third, touches in the box, final-third entries and progressive passes show whether possession went forward. High possession with few of these is the classic sign of sterile possession against a deep block.',
    watch: 'Entries and box touches depend on how the pitch zones were drawn from one camera; treat small gaps as noise.',
    coach: 'The ball is no use to us in front of their block; get it into the box.',
    source: 'I-007, I-013, I-062',
  },
  dispossessed: {
    read: 'Dispossessed counts times a player lost the ball while carrying it. Lower is better. A high count next to many balls won by the opponent means the team was being pressed and lost it under pressure.',
    watch: 'Players who dribble a lot get dispossessed more; read it with dribbles before calling it careless.',
    coach: 'Release it a touch earlier when they close you down.',
    source: 'I-160',
  },
  long_balls: {
    read: 'Long balls are neither good nor bad. Many long balls with low pass accuracy usually means the team went direct to bypass a press, or could not play through midfield.',
    watch: 'Never call more long balls an advantage on its own.',
    coach: 'Go long when it is on, not because we have no other option.',
    source: 'I-032, I-074',
  },
  crosses: {
    read: 'Crosses show how often a side attacked from wide. Many crosses with few shots on target suggests the crossing was not finding anyone.',
    watch: 'More crosses is not automatically better; a team that cannot play through the middle often crosses more.',
    coach: 'Cross early, while their defenders are still running back.',
    source: 'I-011, I-022, P-023',
  },
  shots: {
    read: 'Shots, shots on target and goals show what the attacking produced. High possession with few shots on target reads as sterile possession; a side with less of the ball but more shots on target was more dangerous.',
    watch: 'Goals are few and random in one match; shots and shots on target say more about who created chances. Only mention goals or the score if this match has them.',
    coach: 'Count what our possession produced, not just how much of it we had.',
    source: 'I-062, I-019, I-025',
  },
  xg: {
    read: 'xG (expected goals) adds up how likely each shot was to score. It measures chance quality, not just volume: many shots with low xG means long-range or poor attempts.',
    watch: 'Over one match xG is noisy, and models differ between providers, so never compare our xG with another provider.',
    coach: 'We need better chances, not more of them.',
    source: 'I-007, I-012, G-046',
  },
  dribbles: {
    read: 'Dribbles show players beating opponents with the ball. Many dribbles plus high dispossessed means players tried a lot and lost it often.',
    watch: 'Read with dispossessed: attempts and losses go together.',
    coach: 'Take them on when you have space behind; release it when you do not.',
    source: 'I-113, I-115',
  },
  set_pieces: {
    read: 'Corners show attacking pressure that ended with the ball going out; more corners usually means more time pinning the other side back. Free kicks are neutral: they can be won or conceded.',
    watch: 'Corners rarely decide a match on their own; one or two set pieces can swing the numbers.',
    coach: 'Every corner is a chance; have a plan for it.',
    source: 'G-023, G-024, I-280',
  },
  tackles: {
    read: 'Tackles, interceptions and defensive contribution show defending without the ball. A side with low possession and many of these defended a lot; many interceptions by the opponent is a strong sign a team was being pressed.',
    watch: 'Many defensive actions can mean a team sat deep and had to defend a lot, not that it defended well.',
    coach: 'Win it, then look forward straight away.',
    source: 'I-115, I-141, I-160',
  },
  saves: {
    read: 'Saves are neutral: a goalkeeper with many saves faced many shots on target. Read with the opponent\'s shots on target.',
    watch: 'Never call more saves an advantage for the team.',
    coach: 'If our keeper is busy, the problem is in front of them.',
    source: 'I-284, G-061',
  },
  maps: {
    read: 'Heat maps, pass maps, shot maps and average positions are pictures in the report. Point the user to them for where things happened.',
    watch: 'You cannot see the maps; never describe what they show.',
    coach: 'Have a look at the heat map in the report for where we played.',
    source: 'Trivela report views',
  },
  data_quality: {
    read: 'Every number comes from one camera. Low ball visibility hurts possession, passes and touches most. ID swaps can mix two players into one track. Short clips are small samples.',
    watch: 'Always say when the data is weak. A shaky number stated with confidence is worse than saying "the data is unclear here".',
    coach: 'Trust the trend, not one number.',
    source: 'Trivela engine quality fields',
  },
  not_tracked: {
    read: 'Each match lists the stats it has. Anything not in that list is not tracked for this match. Trivela never measures fouls, cards, offsides or injuries.',
    watch: 'If asked about a stat this match does not have, say it is not tracked for this match and offer the closest stat it does have.',
    coach: 'I don\'t have fouls for this match. I can show you who won the ball back most.',
    source: 'Trivela hard rules 1 and 2',
  },
  reading_a_match: {
    read: 'Read the ball first (possession, passes, accuracy), then what it produced (strikes), then the fight to get it (balls won), then the running (distance, sprints, top speed). The most useful story is a contrast: who had the ball against who did the running, and whether either one led to strikes.',
    watch: 'One match is one match: no trends, no form, no predictions.',
    coach: 'Tell them what happened, why the numbers say so, and what it means for the next game.',
    source: 'I-024, I-013',
  },
};

export const TOPICS = Object.keys(PLAYBOOK);

/* The compact version that goes into every system prompt */
export const PLAYBOOK_TEXT = TOPICS.map(t => `- ${t.replace(/_/g, ' ')}: ${PLAYBOOK[t].read} Watch out: ${PLAYBOOK[t].watch}`).join('\n');
