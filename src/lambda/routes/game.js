import { ncb } from '../../services/nocodebackend.js';

function extractSessionToken(headers = {}) {
  const cookieHeader = headers.cookie || headers.Cookie || '';
  const match = cookieHeader.match(/better-auth\.session_token=([^;]+)/);
  if (match) return match[1];

  const auth = headers.authorization || headers.Authorization || '';
  if (auth.startsWith('Bearer ')) {
    return auth.slice(7).trim();
  }
  return null;
}

function parsePhotos(row) {
  let photos = [];
  if (row.photos_json) {
    try {
      const parsed = typeof row.photos_json === 'string' ? JSON.parse(row.photos_json) : row.photos_json;
      if (Array.isArray(parsed)) {
        photos = parsed.filter((u) => typeof u === 'string' && u.startsWith('http'));
      }
    } catch {
      // ignore parse error
    }
  }
  if (photos.length === 0 && row.image_url && String(row.image_url).startsWith('http')) {
    photos = [row.image_url];
  }
  return photos;
}

function parsePriceHistory(row) {
  if (!row.price_history_json) return [];
  try {
    const parsed = typeof row.price_history_json === 'string'
      ? JSON.parse(row.price_history_json)
      : row.price_history_json;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function formatEffectiveDate(val) {
  if (!val) return new Date().toISOString().slice(0, 10);
  const str = String(val);
  if (str.length >= 10) return str.slice(0, 10);
  return new Date().toISOString().slice(0, 10);
}

/**
 * GET /api/game/houses
 * Returns a shuffled batch of playable house listings with all carousel photos,
 * location (City, State), square footage, listed price, and price snapshot date.
 */
export async function getGameHousesHandler(req) {
  try {
    const query = req.queryStringParameters || {};
    const requestedLimit = Math.min(Math.max(parseInt(query.limit || '25', 10), 1), 60);

    // Pick 4 distinct random pages across the 68 pages (3,400+ rows across 35 states) in `sales`
    const totalPages = 68;
    const chosenPages = new Set();
    while (chosenPages.size < 4) {
      chosenPages.add(1 + Math.floor(Math.random() * totalPages));
    }

    const pageFetches = Array.from(chosenPages).map((page) =>
      ncb.read('sales', { limit: 30, page }).catch(() => null)
    );
    const results = await Promise.all(pageFetches);

    let rows = [];
    for (const res of results) {
      const data = Array.isArray(res?.data) ? res.data : (Array.isArray(res) ? res : []);
      rows.push(...data);
    }

    if (rows.length === 0) {
      const fallback = await ncb.read('sales', { limit: 50, page: 1 });
      rows = Array.isArray(fallback?.data) ? fallback.data : (Array.isArray(fallback) ? fallback : []);
    }

    // Filter to playable houses: must have photos, valid listed price > 0, valid sqft > 0, and city/state
    const seenIds = new Set();
    const playable = rows
      .map((row) => {
        const photos = parsePhotos(row);
        const price = Number(row.price) || 0;
        const sqft = Number(row.sqft) || 0;
        const city = (row.city || '').trim();
        const state = (row.state || '').trim();

        return {
          id: row.id,
          sale_id: row.id,
          zpid: row.zpid || String(row.id),
          city,
          state,
          zipcode: row.zipcode || '',
          address: row.address || `${city}, ${state}`,
          sqft,
          bedrooms: row.bedrooms ? Number(row.bedrooms) : null,
          bathrooms: row.bathrooms ? Number(row.bathrooms) : null,
          property_type: row.property_type || 'SINGLE_FAMILY',
          listing_category: row.listing_category || 'FOR_SALE',
          home_status: row.home_status || 'FOR_SALE',
          price,
          price_thousands: Math.round(price / 1000),
          price_display: row.price_display || `$${price.toLocaleString()}`,
          photos,
          photo_count: photos.length,
          image_url: photos[0] || row.image_url || '',
          price_effective_date: formatEffectiveDate(row.scraped_at),
          price_history: parsePriceHistory(row),
          scraped_at: row.scraped_at || null,
        };
      })
      .filter((h) => {
        if (h.price < 30000 || h.sqft <= 100 || !h.city || !h.state || h.photos.length === 0) {
          return false;
        }
        const key = `${h.zpid}_${h.id}`;
        if (seenIds.has(key)) return false;
        seenIds.add(key);
        return true;
      });

    // Shuffle all playable houses
    for (let i = playable.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [playable[i], playable[j]] = [playable[j], playable[i]];
    }

    // Interleave by state so consecutive houses come from different states & cities
    const byState = new Map();
    for (const h of playable) {
      if (!byState.has(h.state)) byState.set(h.state, []);
      byState.get(h.state).push(h);
    }
    const interleaved = [];
    let added = true;
    while (added && interleaved.length < requestedLimit) {
      added = false;
      for (const [, list] of byState) {
        if (list.length > 0) {
          interleaved.push(list.shift());
          added = true;
          if (interleaved.length >= requestedLimit) break;
        }
      }
    }

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ok: true,
        count: interleaved.length,
        houses: interleaved,
      }),
    };
  } catch (err) {
    return {
      statusCode: 500,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: err.message }),
    };
  }
}

/**
 * POST /api/game/guess
 * Records a player's guess into `game_guesses`, updates `player_profiles` and `leaderboards`,
 * and tracks `house_price_snapshots` for date-based price history.
 */
export async function submitGuessHandler(req) {
  try {
    const sessionToken = extractSessionToken(req.headers);
    const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});

    const saleId = Number(body.sale_id || body.id || 0);
    const zpid = String(body.zpid || saleId || '');
    const guessThousands = Number(body.guess_thousands ?? body.guess_k ?? 500);
    const guessPrice = Math.round(guessThousands * 1000);
    const actualListedPrice = Number(body.actual_listed_price ?? body.actual_price ?? 0);

    if (!actualListedPrice || actualListedPrice <= 0) {
      return {
        statusCode: 400,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ok: false, error: 'Valid actual_listed_price is required.' }),
      };
    }

    const diffDollars = guessPrice - actualListedPrice;
    const absDiffDollars = Math.abs(diffDollars);
    const pctOff = Number(((absDiffDollars / actualListedPrice) * 100).toFixed(2));
    const direction = diffDollars === 0 ? 'EXACT' : (diffDollars > 0 ? 'OVER' : 'UNDER');

    // Accuracy score out of 1,000 points: 1000 for exact, scaling down with % error
    const pointsEarned = Math.max(0, Math.round(1000 * Math.pow(Math.max(0, 1 - Math.min(pctOff, 100) / 100), 1.3)));

    const guestToken = String(body.guest_token || 'anonymous_guest').slice(0, 120);
    const userId = body.user_id ? String(body.user_id) : null;
    const playerName = String(body.player_name || 'Guest Player').trim().slice(0, 100) || 'Guest Player';
    const city = String(body.city || '').slice(0, 100);
    const state = String(body.state || '').slice(0, 50);
    const sqft = body.sqft ? Number(body.sqft) : null;
    const imageUrl = String(body.image_url || '').slice(0, 500);
    const priceEffectiveDate = formatEffectiveDate(body.price_effective_date);
    const nowSql = new Date().toISOString().slice(0, 19).replace('T', ' ');

    // 1. Save the individual guess result into `game_guesses`
    const guessPayload = {
      user_id: userId,
      player_id: body.player_id ? Number(body.player_id) : null,
      guest_token: guestToken,
      player_name: playerName,
      sale_id: saleId,
      zpid,
      snapshot_id: body.snapshot_id ? Number(body.snapshot_id) : null,
      price_effective_date: priceEffectiveDate,
      city,
      state,
      sqft,
      image_url: imageUrl,
      guess_thousands: guessThousands,
      guess_price: guessPrice,
      actual_listed_price: actualListedPrice,
      diff_dollars: diffDollars,
      abs_diff_dollars: absDiffDollars,
      pct_off: pctOff,
      direction,
      points_earned: pointsEarned,
      guessed_at: nowSql,
    };

    const createdGuess = await ncb.create('game_guesses', guessPayload, { sessionToken });

    // 2. Update or create `player_profiles` & `leaderboards` asynchronously
    let updatedProfile = null;
    try {
      const profilesRes = await ncb.read('player_profiles', { limit: 100 });
      const profiles = Array.isArray(profilesRes?.data) ? profilesRes.data : [];
      const existingProfile = profiles.find(
        (p) => (userId && p.user_id === userId) || (guestToken && p.guest_token === guestToken)
      );

      if (existingProfile) {
        const totalGuesses = (Number(existingProfile.total_guesses) || 0) + 1;
        const totalPoints = (Number(existingProfile.total_points) || 0) + pointsEarned;
        const cumAbsDiff = (Number(existingProfile.cumulative_abs_diff_dollars) || 0) + absDiffDollars;
        const avgAbsDiff = Math.round(cumAbsDiff / totalGuesses);
        const prevAvgPct = Number(existingProfile.avg_pct_off) || 0;
        const newAvgPct = Number((((prevAvgPct * (totalGuesses - 1)) + pctOff) / totalGuesses).toFixed(2));
        const prevBest = existingProfile.best_diff_dollars !== null && existingProfile.best_diff_dollars !== undefined
          ? Number(existingProfile.best_diff_dollars)
          : absDiffDollars;
        const bestDiff = Math.min(prevBest, absDiffDollars);
        const isGoodGuess = pctOff <= 15;
        const currentStreak = isGoodGuess ? ((Number(existingProfile.current_streak) || 0) + 1) : 0;
        const bestStreak = Math.max(Number(existingProfile.best_streak) || 0, currentStreak);

        await ncb.update('player_profiles', existingProfile.id, {
          display_name: playerName !== 'Guest Player' ? playerName : (existingProfile.display_name || playerName),
          user_id: userId || existingProfile.user_id || null,
          is_authenticated: userId ? 1 : (existingProfile.is_authenticated || 0),
          total_guesses: totalGuesses,
          total_points: totalPoints,
          cumulative_abs_diff_dollars: cumAbsDiff,
          avg_abs_diff_dollars: avgAbsDiff,
          avg_pct_off: newAvgPct,
          best_diff_dollars: bestDiff,
          current_streak: currentStreak,
          best_streak: bestStreak,
          last_played_at: nowSql,
        });

        updatedProfile = {
          id: existingProfile.id,
          total_guesses: totalGuesses,
          total_points: totalPoints,
          avg_abs_diff_dollars: avgAbsDiff,
          avg_pct_off: newAvgPct,
          best_diff_dollars: bestDiff,
          current_streak: currentStreak,
          best_streak: bestStreak,
        };
      } else {
        const isGoodGuess = pctOff <= 15;
        const newProf = await ncb.create('player_profiles', {
          user_id: userId,
          guest_token: guestToken,
          display_name: playerName,
          is_authenticated: userId ? 1 : 0,
          total_guesses: 1,
          total_points: pointsEarned,
          cumulative_abs_diff_dollars: absDiffDollars,
          avg_abs_diff_dollars: absDiffDollars,
          avg_pct_off: pctOff,
          best_diff_dollars: absDiffDollars,
          current_streak: isGoodGuess ? 1 : 0,
          best_streak: isGoodGuess ? 1 : 0,
          last_played_at: nowSql,
        });
        updatedProfile = {
          id: newProf?.id || null,
          total_guesses: 1,
          total_points: pointsEarned,
          avg_abs_diff_dollars: absDiffDollars,
          avg_pct_off: pctOff,
          best_diff_dollars: absDiffDollars,
          current_streak: isGoodGuess ? 1 : 0,
          best_streak: isGoodGuess ? 1 : 0,
        };
      }

      // Sync ALL_TIME leaderboard entry
      const lbRes = await ncb.read('leaderboards', { limit: 100 });
      const lbRows = Array.isArray(lbRes?.data) ? lbRes.data : [];
      const existingLb = lbRows.find(
        (row) =>
          row.period_type === 'ALL_TIME' &&
          ((userId && row.user_id === userId) || (guestToken && row.guest_token === guestToken))
      );

      if (existingLb && updatedProfile) {
        await ncb.update('leaderboards', existingLb.id, {
          display_name: playerName !== 'Guest Player' ? playerName : (existingLb.display_name || playerName),
          user_id: userId || existingLb.user_id || null,
          player_id: updatedProfile.id || existingLb.player_id,
          guesses_count: updatedProfile.total_guesses,
          total_points: updatedProfile.total_points,
          total_abs_diff_dollars: updatedProfile.avg_abs_diff_dollars * updatedProfile.total_guesses,
          avg_abs_diff_dollars: updatedProfile.avg_abs_diff_dollars,
          avg_pct_off: updatedProfile.avg_pct_off,
          closest_diff_dollars: updatedProfile.best_diff_dollars,
        });
      } else if (updatedProfile) {
        await ncb.create('leaderboards', {
          period_type: 'ALL_TIME',
          period_date: 'ALL',
          user_id: userId,
          player_id: updatedProfile.id,
          guest_token: guestToken,
          display_name: playerName,
          guesses_count: updatedProfile.total_guesses,
          total_points: updatedProfile.total_points,
          total_abs_diff_dollars: absDiffDollars,
          avg_abs_diff_dollars: updatedProfile.avg_abs_diff_dollars,
          avg_pct_off: updatedProfile.avg_pct_off,
          closest_diff_dollars: updatedProfile.best_diff_dollars,
        });
      }
    } catch (statsErr) {
      console.warn('Non-fatal profile/leaderboard sync warning:', statsErr.message);
    }

    return {
      statusCode: 201,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ok: true,
        result: {
          ...guessPayload,
          id: createdGuess?.id || null,
        },
        profile: updatedProfile,
      }),
    };
  } catch (err) {
    return {
      statusCode: 500,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: err.message }),
    };
  }
}

/**
 * GET /api/game/results
 * Returns recent guess results (house vs guess vs how far off) for the player or globally.
 */
export async function getGameResultsHandler(req) {
  try {
    const query = req.queryStringParameters || {};
    const guestToken = query.guest_token || null;
    const userId = query.user_id || null;

    const res = await ncb.read('game_guesses', { limit: 100 });
    const allRows = Array.isArray(res?.data) ? res.data : (Array.isArray(res) ? res : []);

    // Sort newest first
    allRows.sort((a, b) => (Number(b.id) || 0) - (Number(a.id) || 0));

    const playerResults = (guestToken || userId)
      ? allRows.filter((r) => (userId && r.user_id === userId) || (guestToken && r.guest_token === guestToken))
      : allRows;

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ok: true,
        playerResults: playerResults.slice(0, 50),
        recentGlobalResults: allRows.slice(0, 25),
      }),
    };
  } catch (err) {
    return {
      statusCode: 500,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: err.message }),
    };
  }
}

/**
 * GET /api/game/leaderboard
 * Returns leaderboard rankings and player profile stats.
 */
export async function getLeaderboardHandler(req) {
  try {
    const query = req.queryStringParameters || {};
    const periodType = query.period_type || 'ALL_TIME';

    const res = await ncb.read('leaderboards', { limit: 100 });
    const rows = Array.isArray(res?.data) ? res.data : (Array.isArray(res) ? res : []);

    const filtered = rows
      .filter((r) => !periodType || r.period_type === periodType)
      .sort((a, b) => {
        const ptsDiff = (Number(b.total_points) || 0) - (Number(a.total_points) || 0);
        if (ptsDiff !== 0) return ptsDiff;
        return (Number(a.avg_abs_diff_dollars) || 0) - (Number(b.avg_abs_diff_dollars) || 0);
      });

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ok: true,
        period_type: periodType,
        leaderboard: filtered.slice(0, 50),
      }),
    };
  } catch (err) {
    return {
      statusCode: 500,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: err.message }),
    };
  }
}

/**
 * GET /api/game/price-history
 * Fetches date-based price snapshots for a house (zpid or sale_id) from `house_price_snapshots`.
 */
export async function getHousePriceSnapshotsHandler(req) {
  try {
    const query = req.queryStringParameters || {};
    const zpid = query.zpid ? String(query.zpid) : null;
    const saleId = query.sale_id ? Number(query.sale_id) : null;

    const res = await ncb.read('house_price_snapshots', { limit: 100 });
    const rows = Array.isArray(res?.data) ? res.data : (Array.isArray(res) ? res : []);

    const filtered = (zpid || saleId)
      ? rows.filter((r) => (zpid && String(r.zpid) === zpid) || (saleId && Number(r.sale_id) === saleId))
      : rows.slice(0, 25);

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ok: true,
        snapshots: filtered,
      }),
    };
  } catch (err) {
    return {
      statusCode: 500,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: err.message }),
    };
  }
}

/**
 * POST /api/game/player
 * Updates a player's display name or links a guest_token to an authenticated user_id.
 */
export async function updatePlayerProfileHandler(req) {
  try {
    const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});
    const guestToken = String(body.guest_token || '').trim();
    const userId = body.user_id ? String(body.user_id).trim() : null;
    const displayName = String(body.display_name || 'Guest Player').trim().slice(0, 100);
    const email = body.email ? String(body.email).trim().slice(0, 255) : null;

    if (!guestToken && !userId) {
      return {
        statusCode: 400,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ok: false, error: 'guest_token or user_id is required.' }),
      };
    }

    const profilesRes = await ncb.read('player_profiles', { limit: 100 });
    const profiles = Array.isArray(profilesRes?.data) ? profilesRes.data : [];
    const existing = profiles.find(
      (p) => (userId && p.user_id === userId) || (guestToken && p.guest_token === guestToken)
    );

    let profile;
    if (existing) {
      await ncb.update('player_profiles', existing.id, {
        display_name: displayName,
        user_id: userId || existing.user_id || null,
        email: email || existing.email || null,
        is_authenticated: (userId || email) ? 1 : (existing.is_authenticated || 0),
      });
      profile = { ...existing, display_name: displayName, email: email || existing.email };
    } else {
      const created = await ncb.create('player_profiles', {
        user_id: userId,
        guest_token: guestToken,
        display_name: displayName,
        email,
        is_authenticated: (userId || email) ? 1 : 0,
        total_guesses: 0,
        total_points: 0,
        cumulative_abs_diff_dollars: 0,
        avg_abs_diff_dollars: 0,
        avg_pct_off: 0,
      });
      profile = { id: created?.id, guest_token: guestToken, display_name: displayName, email };
    }

    // Also update display_name on leaderboard entry if present
    const lbRes = await ncb.read('leaderboards', { limit: 100 });
    const lbRows = Array.isArray(lbRes?.data) ? lbRes.data : [];
    const existingLb = lbRows.find(
      (r) => (userId && r.user_id === userId) || (guestToken && r.guest_token === guestToken)
    );
    if (existingLb) {
      await ncb.update('leaderboards', existingLb.id, {
        display_name: displayName,
        user_id: userId || existingLb.user_id || null,
      });
    }

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: true, profile }),
    };
  } catch (err) {
    return {
      statusCode: 500,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: err.message }),
    };
  }
}
