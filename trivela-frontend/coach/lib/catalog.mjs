/* Every stat Trivela reports or plans to report. The coach uses whichever ones a match actually has,
   so a new engine stat works as soon as it is saved, and a missing one is "not tracked for this match".
   kind: how a gap is judged (possession, points = other percentages, kmh, xg, share = counts and distances)
   better: higher | lower | neutral (neutral = more is not automatically good, e.g. saves, long balls)
   group: what the stat says about the game. topic: the playbook entry that explains it. */

export const CATALOG = [
  { stat: 'Possession', unit: '%', kind: 'possession', better: 'higher', group: 'possession', topic: 'possession' },
  { stat: 'Touches', unit: '', kind: 'share', better: 'higher', group: 'possession', topic: 'touches' },
  { stat: 'Time in final third', unit: 'min', kind: 'share', better: 'higher', group: 'possession', topic: 'final_third' },
  { stat: 'Touches in opposition box', unit: '', kind: 'share', better: 'higher', group: 'attacking', topic: 'final_third', aliases: ['Touches in box'] },
  { stat: 'Dispossessed', unit: '', kind: 'share', better: 'lower', group: 'possession', topic: 'dispossessed' },

  { stat: 'Passes', unit: '', kind: 'share', better: 'higher', group: 'possession', topic: 'passing' },
  { stat: 'Pass accuracy', unit: '%', kind: 'points', better: 'higher', group: 'possession', topic: 'pass_accuracy', aliases: ['Passing accuracy'] },
  { stat: 'Progressive passes', unit: '', kind: 'share', better: 'higher', group: 'attacking', topic: 'final_third' },
  { stat: 'Final-third entries', unit: '', kind: 'share', better: 'higher', group: 'attacking', topic: 'final_third' },
  { stat: 'Long balls', unit: '', kind: 'share', better: 'neutral', group: 'possession', topic: 'long_balls' },
  { stat: 'Crosses', unit: '', kind: 'share', better: 'neutral', group: 'attacking', topic: 'crosses' },

  { stat: 'Shots', unit: '', kind: 'share', better: 'higher', group: 'attacking', topic: 'shots' },
  { stat: 'Shots on target', unit: '', kind: 'share', better: 'higher', group: 'attacking', topic: 'shots' },
  { stat: 'Goals', unit: '', kind: 'share', better: 'higher', group: 'attacking', topic: 'shots' },
  { stat: 'xG', unit: '', kind: 'xg', better: 'higher', group: 'attacking', topic: 'xg' },
  { stat: 'Strikes', unit: '', kind: 'share', better: 'higher', group: 'attacking', topic: 'strikes' },
  { stat: 'Dribbles', unit: '', kind: 'share', better: 'higher', group: 'attacking', topic: 'dribbles' },
  { stat: 'Corners', unit: '', kind: 'share', better: 'higher', group: 'set_pieces', topic: 'set_pieces' },
  { stat: 'Free kicks', unit: '', kind: 'share', better: 'neutral', group: 'set_pieces', topic: 'set_pieces', aliases: ['Freekicks'] },

  { stat: 'Balls won', unit: '', kind: 'share', better: 'higher', group: 'defending', topic: 'balls_won' },
  { stat: 'Tackles', unit: '', kind: 'share', better: 'higher', group: 'defending', topic: 'tackles' },
  { stat: 'Interceptions', unit: '', kind: 'share', better: 'higher', group: 'defending', topic: 'tackles' },
  { stat: 'Defensive contribution', unit: '', kind: 'share', better: 'higher', group: 'defending', topic: 'tackles' },
  { stat: 'Clearances', unit: '', kind: 'share', better: 'neutral', group: 'defending', topic: 'tackles' },
  { stat: 'Saves', unit: '', kind: 'share', better: 'neutral', group: 'goalkeeping', topic: 'saves' },
  { stat: 'Fastest strike', unit: 'km/h', kind: 'kmh', better: 'neutral', group: 'attacking', topic: 'strikes' },

  { stat: 'Distance covered', unit: 'km', kind: 'share', better: 'higher', group: 'physical', topic: 'distance' },
  { stat: 'High-intensity distance', unit: 'km', kind: 'share', better: 'higher', group: 'physical', topic: 'sprints' },
  { stat: 'Top speed', unit: 'km/h', kind: 'kmh', better: 'higher', group: 'physical', topic: 'top_speed', aliases: ['Sprint speed'] },
  { stat: 'Sprints >25 km/h', unit: '', kind: 'share', better: 'higher', group: 'physical', topic: 'sprints', aliases: ['Sprints', 'nb of sprints'] },
  { stat: 'Avg sprint speed', unit: 'km/h', kind: 'kmh', better: 'higher', group: 'physical', topic: 'top_speed' },
];

/* Pictures in the report, not numbers. The coach can point to them but cannot read them yet */
export const VISUALS = ['Heat maps', 'Pass map', 'Shot map', 'Average positions'];

const BY_NAME = new Map(CATALOG.flatMap(d => [d.stat, ...(d.aliases || [])].map(n => [n.toLowerCase(), d])));

/* A stat the catalog doesn't know yet still gets compared, without judging which side is better */
export const describe = name => BY_NAME.get(String(name).toLowerCase())
  || { stat: String(name), unit: '', kind: 'share', better: 'neutral', group: 'other', topic: null };
