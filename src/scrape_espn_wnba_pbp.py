import argparse
import itertools
import re
import requests
import pandas as pd


WNBA_REGULATION_PERIOD_SECONDS = 600
WNBA_OT_PERIOD_SECONDS = 300


def clean_play_text(text):
    if pd.isna(text):
        return ""

    text = str(text)
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def build_team_lookup(data):
    team_lookup = {}

    competitions = data.get("header", {}).get("competitions", [])

    if competitions:
        competitors = competitions[0].get("competitors", [])

        for competitor in competitors:
            team = competitor.get("team", {})
            team_id = str(team.get("id"))

            team_lookup[team_id] = {
                "team_id": team.get("id"),
                "team_name": team.get("displayName"),
                "team_short_name": team.get("shortDisplayName"),
                "team_abbrev": team.get("abbreviation"),
                "home_away": competitor.get("homeAway")
            }

    return team_lookup


def build_team_lookup_by_abbrev(team_lookup):
    lookup = {}

    for _, team in team_lookup.items():
        abbrev = team.get("team_abbrev")
        if abbrev:
            lookup[abbrev] = team

    return lookup


def clock_to_seconds_remaining(clock):
    if pd.isna(clock):
        return None

    clock = str(clock).strip()

    # ESPN uses both "M:SS" and late-quarter decimal seconds like "44.4"
    mmss_match = re.match(r"^(\d+):(\d{1,2})$", clock)
    if mmss_match:
        minutes = int(mmss_match.group(1))
        seconds = int(mmss_match.group(2))
        return minutes * 60 + seconds

    seconds_match = re.match(r"^(\d+(?:\.\d+)?)$", clock)
    if seconds_match:
        return float(seconds_match.group(1))

    return None


def calculate_elapsed_seconds(period, clock):
    seconds_remaining = clock_to_seconds_remaining(clock)

    if pd.isna(period) or seconds_remaining is None:
        return None

    period = int(period)

    if period <= 4:
        period_length = WNBA_REGULATION_PERIOD_SECONDS
        elapsed_before_period = (period - 1) * WNBA_REGULATION_PERIOD_SECONDS
    else:
        period_length = WNBA_OT_PERIOD_SECONDS
        elapsed_before_period = 4 * WNBA_REGULATION_PERIOD_SECONDS + (period - 5) * WNBA_OT_PERIOD_SECONDS

    elapsed_in_period = period_length - seconds_remaining

    return elapsed_before_period + elapsed_in_period


def extract_name_before_keyword(text, keywords):
    for keyword in keywords:
        pattern = rf"^(.*?)\s+{keyword}"
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            return match.group(1).strip()

    return None


def extract_parenthetical_player(text, action_words):
    parenthetical_matches = re.findall(r"\((.*?)\)", text)

    for item in parenthetical_matches:
        for action_word in action_words:
            pattern = rf"^(.*?)\s+{action_word}"
            match = re.search(pattern, item, flags=re.IGNORECASE)

            if match:
                return match.group(1).strip()

    return None


def extract_shot_distance(text):
    match = re.search(r"(\d+)-foot", text, flags=re.IGNORECASE)

    if match:
        return int(match.group(1))

    return None


def classify_shot_type(text, play_type_text):
    text_lower = str(text).lower()
    play_type_lower = str(play_type_text).lower()

    combined = f"{text_lower} {play_type_lower}"

    if "free throw" in combined:
        return "Free Throw"

    if "three point" in combined or "3pt" in combined:
        return "Three Pointer"

    if "layup" in combined:
        return "Layup"

    if "dunk" in combined:
        return "Dunk"

    if "hook" in combined:
        return "Hook Shot"

    if "tip" in combined:
        return "Tip Shot"

    if "jumper" in combined or "jump shot" in combined or "pullup" in combined:
        return "Jump Shot"

    if "floating" in combined or "floater" in combined:
        return "Floater"

    if "bank" in combined:
        return "Bank Shot"

    if "shot" in combined:
        return "Shot"

    return None


def classify_event_category(text, play_type_text, scoring_play, shooting_play):
    text_lower = str(text).lower()
    play_type_lower = str(play_type_text).lower()

    combined = f"{text_lower} {play_type_lower}"

    if "enters the game for" in combined:
        return "Substitution"

    if "timeout" in combined:
        return "Timeout"

    if "jumpball" in combined or "jump ball" in combined or " vs. " in text_lower:
        return "Jump Ball"

    if "turnover" in combined:
        return "Turnover"

    if "rebound" in combined:
        return "Rebound"

    if "foul" in combined:
        return "Foul"

    if "free throw" in combined:
        return "Free Throw"

    if "violation" in combined:
        return "Violation"

    if "block" in combined:
        return "Block"

    if "steal" in combined:
        return "Steal"

    if "end of" in combined:
        return "Period End"

    if "start of" in combined:
        return "Period Start"

    if shooting_play:
        return "Shot"

    if scoring_play:
        return "Scoring Play"

    return "Other"


def extract_players(text, play_type_text):
    text = clean_play_text(text)

    primary_player = None
    secondary_player = None
    assist_player = None
    steal_player = None
    block_player = None
    sub_in = None
    sub_out = None
    rebound_player = None
    foul_player = None
    turnover_player = None
    jumpball_player_1 = None
    jumpball_player_2 = None
    possession_gained_by = None

    sub_match = re.search(r"^(.*?) enters the game for (.*?)$", text, flags=re.IGNORECASE)
    if sub_match:
        sub_in = sub_match.group(1).strip()
        sub_out = sub_match.group(2).strip()
        primary_player = sub_in
        secondary_player = sub_out

    jump_match = re.search(r"^(.*?) vs\. (.*?) \((.*?) gains possession\)", text, flags=re.IGNORECASE)
    if jump_match:
        jumpball_player_1 = jump_match.group(1).strip()
        jumpball_player_2 = jump_match.group(2).strip()
        possession_gained_by = jump_match.group(3).strip()
        primary_player = jumpball_player_1
        secondary_player = jumpball_player_2

    assist_player = extract_parenthetical_player(text, ["assists", "assist"])
    steal_player = extract_parenthetical_player(text, ["steals", "steal"])
    block_player = extract_parenthetical_player(text, ["blocks", "block"])

    if "turnover" in text.lower():
        turnover_player = extract_name_before_keyword(
            text,
            ["bad pass", "lost ball", "traveling", "offensive foul", "turnover"]
        )
        primary_player = primary_player or turnover_player

    if "rebound" in text.lower():
        rebound_player = extract_name_before_keyword(
            text,
            ["offensive rebound", "defensive rebound", "rebound"]
        )
        primary_player = primary_player or rebound_player

    if "foul" in text.lower():
        foul_player = extract_name_before_keyword(
            text,
            [
                "shooting foul",
                "personal foul",
                "offensive foul",
                "loose ball foul",
                "technical foul",
                "flagrant foul",
                "foul"
            ]
        )
        primary_player = primary_player or foul_player

    if "free throw" in text.lower():
        primary_player = primary_player or extract_name_before_keyword(
            text,
            ["makes free throw", "misses free throw"]
        )

    if "makes" in text.lower() or "misses" in text.lower():
        primary_player = primary_player or extract_name_before_keyword(text, ["makes", "misses"])

    if assist_player:
        secondary_player = secondary_player or assist_player

    if steal_player:
        secondary_player = secondary_player or steal_player

    if block_player:
        secondary_player = secondary_player or block_player

    return {
        "primary_player": primary_player,
        "secondary_player": secondary_player,
        "assist_player": assist_player,
        "steal_player": steal_player,
        "block_player": block_player,
        "sub_in": sub_in,
        "sub_out": sub_out,
        "rebound_player": rebound_player,
        "foul_player": foul_player,
        "turnover_player": turnover_player,
        "jumpball_player_1": jumpball_player_1,
        "jumpball_player_2": jumpball_player_2,
        "possession_gained_by": possession_gained_by
    }


def classify_shot_result(text, shooting_play, scoring_play):
    text_lower = str(text).lower()

    if not shooting_play and "free throw" not in text_lower:
        return None

    if "makes" in text_lower:
        return "Made"

    if "misses" in text_lower:
        return "Missed"

    if scoring_play:
        return "Made"

    return None


def extract_starters_from_boxscore(data, team_lookup):
    starters = {}

    team_id_to_abbrev = {}

    for team_id, info in team_lookup.items():
        team_id_to_abbrev[str(team_id)] = info.get("team_abbrev")

    boxscore_players = data.get("boxscore", {}).get("players", [])

    for team_block in boxscore_players:
        team = team_block.get("team", {})
        team_id = str(team.get("id"))
        team_abbrev = team.get("abbreviation") or team_id_to_abbrev.get(team_id)

        if not team_abbrev:
            continue

        starters[team_abbrev] = []

        statistics_groups = team_block.get("statistics", [])

        for group in statistics_groups:
            group_name = str(group.get("name", "")).lower()
            athletes = group.get("athletes", [])

            if group_name == "starters":
                for athlete_entry in athletes:
                    athlete = athlete_entry.get("athlete", {})
                    name = athlete.get("displayName") or athlete.get("shortName")
                    if name:
                        starters[team_abbrev].append(name)

        if len(starters[team_abbrev]) < 5:
            for group in statistics_groups:
                athletes = group.get("athletes", [])

                for athlete_entry in athletes:
                    is_starter = athlete_entry.get("starter")

                    if is_starter is True:
                        athlete = athlete_entry.get("athlete", {})
                        name = athlete.get("displayName") or athlete.get("shortName")

                        if name and name not in starters[team_abbrev]:
                            starters[team_abbrev].append(name)

        starters[team_abbrev] = starters[team_abbrev][:5]

    return starters


def sorted_lineup(players):
    players = [p for p in players if p]
    return sorted(players)


def lineup_string(players):
    players = sorted_lineup(players)
    return " | ".join(players)


def lineup_id(players):
    players = sorted_lineup(players)
    return " || ".join(players)


def make_three_player_groups(players):
    players = sorted_lineup(players)

    if len(players) < 3:
        return []

    groups = []

    for combo in itertools.combinations(players, 3):
        groups.append(" | ".join(combo))

    return groups


def normalize_player_name(name):
    if pd.isna(name) or name is None:
        return ""

    name = str(name).strip().lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


def find_matching_player(target_name, player_list):
    target_norm = normalize_player_name(target_name)

    if not target_norm:
        return None

    for player in player_list:
        if normalize_player_name(player) == target_norm:
            return player

    target_parts = target_norm.split()
    target_last = target_parts[-1] if target_parts else ""

    for player in player_list:
        player_norm = normalize_player_name(player)
        player_parts = player_norm.split()
        player_last = player_parts[-1] if player_parts else ""

        if target_last and target_last == player_last:
            return player

    return None


def replace_player_in_lineup(lineup, sub_in, sub_out):
    matched_out = find_matching_player(sub_out, lineup)

    if matched_out:
        lineup.remove(matched_out)

        if not find_matching_player(sub_in, lineup):
            lineup.append(sub_in)

        return lineup, True

    return lineup, False


def infer_sub_team(sub_in, sub_out, current_lineups):
    for team_abbrev, players in current_lineups.items():
        if find_matching_player(sub_out, players) or find_matching_player(sub_in, players):
            return team_abbrev

    return None


def add_lineups_to_plays(df, starters, team_lookup):
    df = df.copy()

    df = df.sort_values(
        ["period", "elapsed_seconds", "sequence_number"],
        na_position="last"
    ).reset_index(drop=True)

    team_lookup_by_abbrev = build_team_lookup_by_abbrev(team_lookup)

    home_abbrev = None
    away_abbrev = None

    for abbrev, info in team_lookup_by_abbrev.items():
        if info.get("home_away") == "home":
            home_abbrev = abbrev
        elif info.get("home_away") == "away":
            away_abbrev = abbrev

    team_abbrevs = [abbrev for abbrev in [home_abbrev, away_abbrev] if abbrev]

    current_lineups = {}

    for abbrev in team_abbrevs:
        current_lineups[abbrev] = list(starters.get(abbrev, []))

    lineup_before_records = []
    lineup_after_records = []
    warnings = []

    for _, row in df.iterrows():
        before = {
            team: list(players)
            for team, players in current_lineups.items()
        }

        sub_in = row.get("sub_in")
        sub_out = row.get("sub_out")
        row_team = row.get("team_abbrev")

        if pd.notna(sub_in) and pd.notna(sub_out) and sub_in and sub_out:
            sub_team = row_team if row_team in current_lineups else infer_sub_team(sub_in, sub_out, current_lineups)

            if sub_team in current_lineups:
                lineup = current_lineups[sub_team]

                lineup, replaced = replace_player_in_lineup(lineup, sub_in, sub_out)

                if not replaced:
                    warnings.append(
                        f"Could not replace sub_out '{sub_out}' with sub_in '{sub_in}' in {sub_team} lineup at elapsed_seconds={row.get('elapsed_seconds')}. Current lineup: {lineup}"
                    )

                if len(lineup) > 5:
                    warnings.append(
                        f"{sub_team} lineup has more than 5 players after substitution at elapsed_seconds={row.get('elapsed_seconds')}: {lineup}"
                    )

                if len(lineup) < 5:
                    warnings.append(
                        f"{sub_team} lineup has fewer than 5 players after substitution at elapsed_seconds={row.get('elapsed_seconds')}: {lineup}"
                    )

                current_lineups[sub_team] = lineup
            else:
                warnings.append(
                    f"Could not infer substitution team for '{sub_in}' entering for '{sub_out}' at elapsed_seconds={row.get('elapsed_seconds')}"
                )

        after = {
            team: list(players)
            for team, players in current_lineups.items()
        }

        lineup_before_records.append(before)
        lineup_after_records.append(after)

    for abbrev in team_abbrevs:
        df[f"{abbrev}_lineup_before"] = [
            lineup_string(record.get(abbrev, []))
            for record in lineup_before_records
        ]

        df[f"{abbrev}_lineup_after"] = [
            lineup_string(record.get(abbrev, []))
            for record in lineup_after_records
        ]

        df[f"{abbrev}_lineup_id_before"] = [
            lineup_id(record.get(abbrev, []))
            for record in lineup_before_records
        ]

        df[f"{abbrev}_lineup_id_after"] = [
            lineup_id(record.get(abbrev, []))
            for record in lineup_after_records
        ]

        df[f"{abbrev}_three_player_groups_before"] = [
            "; ".join(make_three_player_groups(record.get(abbrev, [])))
            for record in lineup_before_records
        ]

    if home_abbrev:
        df["home_lineup_before"] = df[f"{home_abbrev}_lineup_before"]
        df["home_lineup_after"] = df[f"{home_abbrev}_lineup_after"]

    if away_abbrev:
        df["away_lineup_before"] = df[f"{away_abbrev}_lineup_before"]
        df["away_lineup_after"] = df[f"{away_abbrev}_lineup_after"]

    def get_event_team_lineup(row):
        abbrev = row.get("team_abbrev")

        if abbrev and f"{abbrev}_lineup_before" in df.columns:
            return row.get(f"{abbrev}_lineup_before")

        return None

    df["event_team_lineup_before"] = df.apply(get_event_team_lineup, axis=1)

    return df, warnings


def create_lineup_stints(df, starters, team_lookup):
    team_lookup_by_abbrev = build_team_lookup_by_abbrev(team_lookup)

    home_abbrev = None
    away_abbrev = None

    for abbrev, info in team_lookup_by_abbrev.items():
        if info.get("home_away") == "home":
            home_abbrev = abbrev
        elif info.get("home_away") == "away":
            away_abbrev = abbrev

    team_abbrevs = [abbrev for abbrev in [home_abbrev, away_abbrev] if abbrev]

    events = []

    for _, row in df.iterrows():
        if row.get("event_category") == "Substitution":
            events.append({
                "elapsed_seconds": row.get("elapsed_seconds"),
                "period": row.get("period"),
                "clock": row.get("clock"),
                "team_abbrev": row.get("team_abbrev"),
                "sub_in": row.get("sub_in"),
                "sub_out": row.get("sub_out"),
                "home_score": row.get("home_score"),
                "away_score": row.get("away_score"),
                "sequence_number": row.get("sequence_number")
            })

    max_elapsed = df["elapsed_seconds"].max()

    if pd.isna(max_elapsed):
        max_elapsed = 2400

    current_lineups = {
        abbrev: list(starters.get(abbrev, []))
        for abbrev in team_abbrevs
    }

    current_start = 0
    current_home_score = 0
    current_away_score = 0

    stints = []

    sorted_events = sorted(
        events,
        key=lambda x: (
            x.get("elapsed_seconds") if pd.notna(x.get("elapsed_seconds")) else 999999,
            x.get("sequence_number") if pd.notna(x.get("sequence_number")) else 999999
        )
    )

    def close_stint(end_time, end_home_score, end_away_score, reason):
        nonlocal current_start, current_home_score, current_away_score

        for team_abbrev, lineup in current_lineups.items():
            if len(lineup) == 0:
                continue

            team_info = team_lookup_by_abbrev.get(team_abbrev, {})
            home_away = team_info.get("home_away")

            if home_away == "home":
                points_for = end_home_score - current_home_score
                points_against = end_away_score - current_away_score
            else:
                points_for = end_away_score - current_away_score
                points_against = end_home_score - current_home_score

            stints.append({
                "team_abbrev": team_abbrev,
                "home_away": home_away,
                "lineup_id": lineup_id(lineup),
                "lineup": lineup_string(lineup),
                "player_1": sorted_lineup(lineup)[0] if len(sorted_lineup(lineup)) > 0 else None,
                "player_2": sorted_lineup(lineup)[1] if len(sorted_lineup(lineup)) > 1 else None,
                "player_3": sorted_lineup(lineup)[2] if len(sorted_lineup(lineup)) > 2 else None,
                "player_4": sorted_lineup(lineup)[3] if len(sorted_lineup(lineup)) > 3 else None,
                "player_5": sorted_lineup(lineup)[4] if len(sorted_lineup(lineup)) > 4 else None,
                "start_elapsed_seconds": current_start,
                "end_elapsed_seconds": end_time,
                "duration_seconds": end_time - current_start,
                "duration_minutes": (end_time - current_start) / 60,
                "start_home_score": current_home_score,
                "start_away_score": current_away_score,
                "end_home_score": end_home_score,
                "end_away_score": end_away_score,
                "points_for": points_for,
                "points_against": points_against,
                "plus_minus": points_for - points_against,
                "reason_closed": reason
            })

    for event in sorted_events:
        event_time = event.get("elapsed_seconds")

        if pd.isna(event_time):
            continue

        event_time = float(event_time)

        end_home_score = event.get("home_score")
        end_away_score = event.get("away_score")

        end_home_score = current_home_score if pd.isna(end_home_score) else float(end_home_score)
        end_away_score = current_away_score if pd.isna(end_away_score) else float(end_away_score)

        if event_time > current_start:
            close_stint(event_time, end_home_score, end_away_score, "Substitution")

            current_start = event_time
            current_home_score = end_home_score
            current_away_score = end_away_score

        sub_in = event.get("sub_in")
        sub_out = event.get("sub_out")
        sub_team = event.get("team_abbrev")

        if sub_team not in current_lineups:
            sub_team = infer_sub_team(sub_in, sub_out, current_lineups)

        if sub_team in current_lineups:
            lineup = current_lineups[sub_team]

            lineup, replaced = replace_player_in_lineup(lineup, sub_in, sub_out)

            if not replaced:
                print(
                    f"Warning: could not replace '{sub_out}' with '{sub_in}' for {sub_team} at {event_time}. Current lineup: {lineup}"
                )

            current_lineups[sub_team] = lineup

    final_home_score = df["home_score"].dropna().iloc[-1] if df["home_score"].notna().any() else current_home_score
    final_away_score = df["away_score"].dropna().iloc[-1] if df["away_score"].notna().any() else current_away_score

    close_stint(float(max_elapsed), float(final_home_score), float(final_away_score), "Game End")

    stints_df = pd.DataFrame(stints)

    return stints_df


def create_three_player_stints(lineup_stints_df):
    rows = []

    for _, row in lineup_stints_df.iterrows():
        lineup = str(row.get("lineup", "")).split(" | ")
        lineup = [player for player in lineup if player and player != "nan"]

        for combo in itertools.combinations(sorted(lineup), 3):
            group_id = " || ".join(combo)
            group_label = " | ".join(combo)

            new_row = row.to_dict()
            new_row["three_player_group_id"] = group_id
            new_row["three_player_group"] = group_label
            new_row["group_player_1"] = combo[0]
            new_row["group_player_2"] = combo[1]
            new_row["group_player_3"] = combo[2]

            rows.append(new_row)

    return pd.DataFrame(rows)


def scrape_espn_wnba_game(game_id):
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary?event={game_id}"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()

    return response.json()


def scrape_espn_wnba_pbp(game_id):
    data = scrape_espn_wnba_game(game_id)

    team_lookup = build_team_lookup(data)
    starters = extract_starters_from_boxscore(data, team_lookup)

    plays = data.get("plays", [])

    if not plays:
        raise ValueError(f"No plays found for game_id={game_id}")

    rows = []

    for play in plays:
        period = play.get("period", {})
        clock = play.get("clock", {})
        team = play.get("team", {})
        play_type = play.get("type", {})

        raw_team_id = team.get("id")
        team_info = team_lookup.get(str(raw_team_id), {})

        text = clean_play_text(play.get("text"))
        play_type_text = play_type.get("text")

        players = extract_players(text, play_type_text)

        row = {
            "game_id": game_id,
            "play_id": play.get("id"),
            "sequence_number": play.get("sequenceNumber"),

            "period": period.get("number"),
            "period_display": period.get("displayValue"),
            "clock": clock.get("displayValue"),
            "seconds_remaining_in_period": clock_to_seconds_remaining(clock.get("displayValue")),
            "elapsed_seconds": calculate_elapsed_seconds(period.get("number"), clock.get("displayValue")),

            "text": text,

            "home_score": play.get("homeScore"),
            "away_score": play.get("awayScore"),

            "scoring_play": play.get("scoringPlay"),
            "score_value": play.get("scoreValue"),
            "shooting_play": play.get("shootingPlay"),

            "wallclock": play.get("wallclock"),

            "team_id": raw_team_id,
            "team_name": team_info.get("team_name"),
            "team_short_name": team_info.get("team_short_name"),
            "team_abbrev": team_info.get("team_abbrev"),
            "home_away": team_info.get("home_away"),

            "play_type_id": play_type.get("id"),
            "play_type_text": play_type_text,

            "x": play.get("coordinate", {}).get("x"),
            "y": play.get("coordinate", {}).get("y"),
        }

        row.update(players)

        row["event_category"] = classify_event_category(
            text=text,
            play_type_text=play_type_text,
            scoring_play=play.get("scoringPlay"),
            shooting_play=play.get("shootingPlay")
        )

        row["shot_result"] = classify_shot_result(
            text=text,
            shooting_play=play.get("shootingPlay"),
            scoring_play=play.get("scoringPlay")
        )

        row["shot_distance"] = extract_shot_distance(text)
        row["shot_type"] = classify_shot_type(text, play_type_text)

        rows.append(row)

    df = pd.DataFrame(rows)

    df = enhance_dataframe(df)

    df, warnings = add_lineups_to_plays(df, starters, team_lookup)
    lineup_stints_df = create_lineup_stints(df, starters, team_lookup)
    three_player_stints_df = create_three_player_stints(lineup_stints_df)

    return df, lineup_stints_df, three_player_stints_df, starters, warnings


def enhance_dataframe(df):
    numeric_cols = [
        "home_score",
        "away_score",
        "score_value",
        "period",
        "elapsed_seconds",
        "seconds_remaining_in_period",
        "x",
        "y"
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "text" in df.columns:
        df["text"] = df["text"].apply(clean_play_text)

    if "elapsed_seconds" not in df.columns or df["elapsed_seconds"].isna().all():
        df["elapsed_seconds"] = df.apply(
            lambda row: calculate_elapsed_seconds(row.get("period"), row.get("clock")),
            axis=1
        )

    if "seconds_remaining_in_period" not in df.columns or df["seconds_remaining_in_period"].isna().all():
        df["seconds_remaining_in_period"] = df["clock"].apply(clock_to_seconds_remaining)

    if "play_type_text" not in df.columns:
        df["play_type_text"] = None

    if "scoring_play" not in df.columns:
        df["scoring_play"] = False

    if "shooting_play" not in df.columns:
        df["shooting_play"] = False

    parsed_players = df.apply(
        lambda row: extract_players(row.get("text"), row.get("play_type_text")),
        axis=1
    )

    parsed_players_df = pd.DataFrame(parsed_players.tolist())

    for col in parsed_players_df.columns:
        df[col] = parsed_players_df[col]

    df["event_category"] = df.apply(
        lambda row: classify_event_category(
            text=row.get("text"),
            play_type_text=row.get("play_type_text"),
            scoring_play=row.get("scoring_play"),
            shooting_play=row.get("shooting_play")
        ),
        axis=1
    )

    df["shot_result"] = df.apply(
        lambda row: classify_shot_result(
            text=row.get("text"),
            shooting_play=row.get("shooting_play"),
            scoring_play=row.get("scoring_play")
        ),
        axis=1
    )

    df["shot_distance"] = df["text"].apply(extract_shot_distance)

    df["shot_type"] = df.apply(
        lambda row: classify_shot_type(row.get("text"), row.get("play_type_text")),
        axis=1
    )

    df = df.sort_values(["period", "elapsed_seconds", "sequence_number"], na_position="last").reset_index(drop=True)

    df["previous_home_score"] = df["home_score"].shift(1).fillna(0)
    df["previous_away_score"] = df["away_score"].shift(1).fillna(0)

    df["points_home"] = df["home_score"] - df["previous_home_score"]
    df["points_away"] = df["away_score"] - df["previous_away_score"]

    df["points_home"] = df["points_home"].clip(lower=0)
    df["points_away"] = df["points_away"].clip(lower=0)

    df["scoring_team"] = None

    if "home_away" in df.columns:
        home_abbrev = df.loc[df["home_away"] == "home", "team_abbrev"].dropna()
        away_abbrev = df.loc[df["home_away"] == "away", "team_abbrev"].dropna()

        home_abbrev = home_abbrev.iloc[0] if len(home_abbrev) else "HOME"
        away_abbrev = away_abbrev.iloc[0] if len(away_abbrev) else "AWAY"

        df.loc[df["points_home"] > 0, "scoring_team"] = home_abbrev
        df.loc[df["points_away"] > 0, "scoring_team"] = away_abbrev

    df["score_margin_home"] = df["home_score"] - df["away_score"]
    df["score_margin_abs"] = df["score_margin_home"].abs()

    df["lead_team"] = "Tie"

    if "home_away" in df.columns:
        home_abbrev = df.loc[df["home_away"] == "home", "team_abbrev"].dropna()
        away_abbrev = df.loc[df["home_away"] == "away", "team_abbrev"].dropna()

        home_abbrev = home_abbrev.iloc[0] if len(home_abbrev) else "HOME"
        away_abbrev = away_abbrev.iloc[0] if len(away_abbrev) else "AWAY"

        df.loc[df["score_margin_home"] > 0, "lead_team"] = home_abbrev
        df.loc[df["score_margin_home"] < 0, "lead_team"] = away_abbrev

    df["game_time_label"] = "Q" + df["period"].astype("Int64").astype(str) + " " + df["clock"].astype(str)

    if "x" in df.columns:
        df.loc[df["x"] < -1000000, "x"] = pd.NA

    if "y" in df.columns:
        df.loc[df["y"] < -1000000, "y"] = pd.NA

    return df

def create_three_player_matchups(three_player_stints_df):
    df = three_player_stints_df.copy()

    join_cols = [
        "start_elapsed_seconds",
        "end_elapsed_seconds",
        "duration_seconds"
    ]

    matchup_df = df.merge(
        df,
        on=join_cols,
        suffixes=("", "_opponent")
    )

    matchup_df = matchup_df[
        matchup_df["team_abbrev"] != matchup_df["team_abbrev_opponent"]
    ].copy()

    matchup_df = matchup_df.rename(columns={
        "team_abbrev_opponent": "opponent_team_abbrev",
        "three_player_group_opponent": "opponent_three_player_group",
        "three_player_group_id_opponent": "opponent_three_player_group_id",
        "group_player_1_opponent": "opponent_group_player_1",
        "group_player_2_opponent": "opponent_group_player_2",
        "group_player_3_opponent": "opponent_group_player_3"
    })

    keep_cols = [
        "team_abbrev",
        "three_player_group",
        "three_player_group_id",
        "group_player_1",
        "group_player_2",
        "group_player_3",

        "opponent_team_abbrev",
        "opponent_three_player_group",
        "opponent_three_player_group_id",
        "opponent_group_player_1",
        "opponent_group_player_2",
        "opponent_group_player_3",

        "start_elapsed_seconds",
        "end_elapsed_seconds",
        "duration_seconds",
        "duration_minutes",

        "points_for",
        "points_against",
        "plus_minus",
        "start_home_score",
        "start_away_score",
        "end_home_score",
        "end_away_score"
    ]

    keep_cols = [col for col in keep_cols if col in matchup_df.columns]

    matchup_df = matchup_df[keep_cols]

    return matchup_df

def create_player_options_from_boxscore(game_id):
    data = scrape_espn_wnba_game(game_id)
    team_lookup = build_team_lookup(data)

    team_id_to_abbrev = {}
    for team_id, info in team_lookup.items():
        team_id_to_abbrev[str(team_id)] = info.get("team_abbrev")

    rows = []

    boxscore_players = data.get("boxscore", {}).get("players", [])

    for team_block in boxscore_players:
        team = team_block.get("team", {})
        team_id = str(team.get("id"))
        team_abbrev = team.get("abbreviation") or team_id_to_abbrev.get(team_id)

        statistics_groups = team_block.get("statistics", [])

        for group in statistics_groups:
            group_name = group.get("name")
            athletes = group.get("athletes", [])

            for athlete_entry in athletes:
                athlete = athlete_entry.get("athlete", {})

                player_name = athlete.get("displayName") or athlete.get("shortName")
                player_id = athlete.get("id")

                stats = athlete_entry.get("stats", [])
                labels = group.get("labels", [])

                stat_map = {}

                for i, label in enumerate(labels):
                    if i < len(stats):
                        stat_map[label] = stats[i]

                minutes = (
                    stat_map.get("MIN")
                    or stat_map.get("Min")
                    or stat_map.get("minutes")
                    or None
                )

                did_not_play = athlete_entry.get("didNotPlay", False)

                if player_name:
                    rows.append({
                        "game_id": game_id,
                        "team_abbrev": team_abbrev,
                        "team_id": team_id,
                        "player_id": player_id,
                        "player_name": player_name,
                        "boxscore_group": group_name,
                        "minutes": minutes,
                        "did_not_play": did_not_play
                    })

    players_df = pd.DataFrame(rows)

    if not players_df.empty:
        players_df = players_df.drop_duplicates(
            subset=["team_abbrev", "player_name"]
        ).sort_values(["team_abbrev", "player_name"])

    return players_df

def elapsed_to_period_clock_label(elapsed_seconds):
    if pd.isna(elapsed_seconds):
        return None

    elapsed_seconds = float(elapsed_seconds)

    if elapsed_seconds < 2400:
        period = int(elapsed_seconds // 600) + 1
        seconds_into_period = elapsed_seconds - ((period - 1) * 600)
        seconds_remaining = 600 - seconds_into_period
        period_label = f"Q{period}"
    else:
        ot_elapsed = elapsed_seconds - 2400
        ot_number = int(ot_elapsed // 300) + 1
        seconds_into_period = ot_elapsed - ((ot_number - 1) * 300)
        seconds_remaining = 300 - seconds_into_period
        period_label = f"OT{ot_number}"

    minutes = int(seconds_remaining // 60)
    seconds = int(seconds_remaining % 60)

    return f"{period_label} {minutes}:{seconds:02d}"


def split_lineup_players(lineup):
    if pd.isna(lineup) or not lineup:
        return []

    return [
        player.strip()
        for player in str(lineup).split(" | ")
        if player.strip()
    ]


def create_rotation_segments(lineup_stints_df):
    rows = []

    for _, row in lineup_stints_df.iterrows():
        players = split_lineup_players(row.get("lineup"))

        for player in players:
            rows.append({
                "team_abbrev": row.get("team_abbrev"),
                "home_away": row.get("home_away"),
                "player_name": player,

                "lineup": row.get("lineup"),
                "lineup_id": row.get("lineup_id"),

                "start_elapsed_seconds": row.get("start_elapsed_seconds"),
                "end_elapsed_seconds": row.get("end_elapsed_seconds"),
                "duration_seconds": row.get("duration_seconds"),
                "duration_minutes": row.get("duration_minutes"),

                "start_elapsed_minutes": row.get("start_elapsed_seconds") / 60,
                "end_elapsed_minutes": row.get("end_elapsed_seconds") / 60,

                "start_game_clock_label": elapsed_to_period_clock_label(row.get("start_elapsed_seconds")),
                "end_game_clock_label": elapsed_to_period_clock_label(row.get("end_elapsed_seconds")),

                "start_home_score": row.get("start_home_score"),
                "start_away_score": row.get("start_away_score"),
                "end_home_score": row.get("end_home_score"),
                "end_away_score": row.get("end_away_score"),

                "points_for": row.get("points_for"),
                "points_against": row.get("points_against"),
                "plus_minus": row.get("plus_minus"),

                "reason_closed": row.get("reason_closed"),
                "started_game": row.get("start_elapsed_seconds") == 0
            })

    rotation_df = pd.DataFrame(rows)

    if rotation_df.empty:
        return rotation_df

    rotation_df = rotation_df.sort_values(
        ["team_abbrev", "player_name", "start_elapsed_seconds", "end_elapsed_seconds"]
    ).reset_index(drop=True)

    return rotation_df


def create_rotation_changes(lineup_stints_df):
    rows = []

    df = lineup_stints_df.copy()
    df = df.sort_values(["team_abbrev", "start_elapsed_seconds", "end_elapsed_seconds"])

    for team_abbrev, team_df in df.groupby("team_abbrev"):
        team_df = team_df.sort_values("start_elapsed_seconds").reset_index(drop=True)

        previous_players = None
        previous_lineup = None

        for stint_number, row in team_df.iterrows():
            current_players = set(split_lineup_players(row.get("lineup")))

            if previous_players is None:
                players_in = sorted(current_players)
                players_out = []
                change_type = "Starting Lineup"
                change_summary = "Starting lineup: " + " | ".join(players_in)
            else:
                players_in = sorted(current_players - previous_players)
                players_out = sorted(previous_players - current_players)

                if not players_in and not players_out:
                    change_type = "No Team Lineup Change"
                    change_summary = "No lineup change for this team"
                else:
                    change_type = "Substitution"
                    change_summary = (
                        "IN: " + ", ".join(players_in)
                        + " / OUT: " + ", ".join(players_out)
                    )

            rows.append({
                "team_abbrev": team_abbrev,
                "home_away": row.get("home_away"),

                "stint_number": stint_number + 1,
                "change_type": change_type,
                "change_summary": change_summary,

                "players_in": ", ".join(players_in),
                "players_out": ", ".join(players_out),

                "previous_lineup": previous_lineup,
                "new_lineup": row.get("lineup"),
                "new_lineup_id": row.get("lineup_id"),

                "start_elapsed_seconds": row.get("start_elapsed_seconds"),
                "end_elapsed_seconds": row.get("end_elapsed_seconds"),
                "duration_seconds": row.get("duration_seconds"),
                "duration_minutes": row.get("duration_minutes"),

                "start_elapsed_minutes": row.get("start_elapsed_seconds") / 60,
                "end_elapsed_minutes": row.get("end_elapsed_seconds") / 60,

                "start_game_clock_label": elapsed_to_period_clock_label(row.get("start_elapsed_seconds")),
                "end_game_clock_label": elapsed_to_period_clock_label(row.get("end_elapsed_seconds")),

                "start_home_score": row.get("start_home_score"),
                "start_away_score": row.get("start_away_score"),
                "end_home_score": row.get("end_home_score"),
                "end_away_score": row.get("end_away_score"),

                "points_for": row.get("points_for"),
                "points_against": row.get("points_against"),
                "plus_minus": row.get("plus_minus")
            })

            previous_players = current_players
            previous_lineup = row.get("lineup")

    changes_df = pd.DataFrame(rows)

    if not changes_df.empty:
        changes_df = changes_df[
            changes_df["change_type"].isin(["Starting Lineup", "Substitution"])
        ].copy()

    return changes_df


def write_outputs(game_id, output_prefix):
    pbp_df, lineup_stints_df, three_player_stints_df, starters, warnings = scrape_espn_wnba_pbp(game_id)

    three_player_matchups_df = create_three_player_matchups(three_player_stints_df)
    player_options_df = create_player_options_from_boxscore(game_id)
    subs_audit_df = pbp_df[pbp_df["event_category"] == "Substitution"].copy()

    rotation_segments_df = create_rotation_segments(lineup_stints_df)
    rotation_changes_df = create_rotation_changes(lineup_stints_df)

    pbp_output = f"{output_prefix}_pbp_enhanced.csv"
    lineup_output = f"{output_prefix}_lineup_stints.csv"
    three_player_output = f"{output_prefix}_three_player_stints.csv"
    three_player_matchup_output = f"{output_prefix}_three_player_matchups.csv"
    player_options_output = f"{output_prefix}_player_options.csv"
    subs_audit_output = f"{output_prefix}_substitution_audit.csv"

    rotation_segments_output = f"{output_prefix}_rotation_segments.csv"
    rotation_changes_output = f"{output_prefix}_rotation_changes.csv"

    pbp_df.to_csv(pbp_output, index=False)
    lineup_stints_df.to_csv(lineup_output, index=False)
    three_player_stints_df.to_csv(three_player_output, index=False)
    three_player_matchups_df.to_csv(three_player_matchup_output, index=False)
    player_options_df.to_csv(player_options_output, index=False)
    subs_audit_df.to_csv(subs_audit_output, index=False)

    rotation_segments_df.to_csv(rotation_segments_output, index=False)
    rotation_changes_df.to_csv(rotation_changes_output, index=False)

    print("")
    print("Saved files:")
    print(f"1. {pbp_output}")
    print(f"2. {lineup_output}")
    print(f"3. {three_player_output}")
    print(f"4. {three_player_matchup_output}")
    print(f"5. {player_options_output}")
    print(f"6. {subs_audit_output}")
    print(f"7. {rotation_segments_output}")
    print(f"8. {rotation_changes_output}")

    print("")
    print("Detected starters:")
    for team, players in starters.items():
        print(f"{team}: {', '.join(players)}")

    print("")
    print("All players found in box score:")
    if not player_options_df.empty:
        for team, group in player_options_df.groupby("team_abbrev"):
            names = group["player_name"].dropna().tolist()
            print(f"{team}: {', '.join(names)}")

    if warnings:
        print("")
        print("Lineup warnings:")
        for warning in warnings[:25]:
            print(f"- {warning}")

        if len(warnings) > 25:
            print(f"...and {len(warnings) - 25} more warnings")

    print("")
    print("Use these files in Tableau:")
    print(f"- {pbp_output}: game flow, scoring runs, player events")
    print(f"- {lineup_output}: full 5-player lineup stints")
    print(f"- {three_player_output}: 3-player group minutes and plus-minus")
    print(f"- {three_player_matchup_output}: selected 3-player group vs opponent 3-player groups")
    print(f"- {player_options_output}: full player dropdown/options list")
    print(f"- {rotation_segments_output}: player rotation timeline / Gantt chart")
    print(f"- {rotation_changes_output}: starting lineup and every coach lineup change")
    pbp_df, lineup_stints_df, three_player_stints_df, starters, warnings = scrape_espn_wnba_pbp(game_id)

    three_player_matchups_df = create_three_player_matchups(three_player_stints_df)
    player_options_df = create_player_options_from_boxscore(game_id)
    subs_audit_df = pbp_df[pbp_df["event_category"] == "Substitution"].copy()

    pbp_output = f"{output_prefix}_pbp_enhanced.csv"
    lineup_output = f"{output_prefix}_lineup_stints.csv"
    three_player_output = f"{output_prefix}_three_player_stints.csv"
    three_player_matchup_output = f"{output_prefix}_three_player_matchups.csv"
    player_options_output = f"{output_prefix}_player_options.csv"
    subs_audit_output = f"{output_prefix}_substitution_audit.csv"

    pbp_df.to_csv(pbp_output, index=False)
    lineup_stints_df.to_csv(lineup_output, index=False)
    three_player_stints_df.to_csv(three_player_output, index=False)
    three_player_matchups_df.to_csv(three_player_matchup_output, index=False)
    player_options_df.to_csv(player_options_output, index=False)
    subs_audit_df.to_csv(subs_audit_output, index=False)

    print("")
    print("Saved files:")
    print(f"1. {pbp_output}")
    print(f"2. {lineup_output}")
    print(f"3. {three_player_output}")
    print(f"4. {three_player_matchup_output}")
    print(f"5. {player_options_output}")
    print(f"6. {subs_audit_output}")

    print("")
    print("Detected starters:")
    for team, players in starters.items():
        print(f"{team}: {', '.join(players)}")

    print("")
    print("All players found in box score:")
    if not player_options_df.empty:
        for team, group in player_options_df.groupby("team_abbrev"):
            names = group["player_name"].dropna().tolist()
            print(f"{team}: {', '.join(names)}")

    if warnings:
        print("")
        print("Lineup warnings:")
        for warning in warnings[:25]:
            print(f"- {warning}")

        if len(warnings) > 25:
            print(f"...and {len(warnings) - 25} more warnings")

    print("")
    print("Use these files in Tableau:")
    print(f"- {pbp_output}: game flow, scoring runs, player events")
    print(f"- {lineup_output}: full 5-player lineup stints")
    print(f"- {three_player_output}: 3-player group minutes and plus-minus")
    print(f"- {three_player_matchup_output}: selected 3-player group vs opponent 3-player groups")
    print(f"- {player_options_output}: full player dropdown/options list")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--game-id",
        type=str,
        required=True,
        help="ESPN game ID to scrape"
    )

    parser.add_argument(
        "--output-prefix",
        type=str,
        default=None,
        help="Output filename prefix"
    )

    args = parser.parse_args()

    output_prefix = args.output_prefix or f"espn_wnba_{args.game_id}"

    write_outputs(args.game_id, output_prefix)


if __name__ == "__main__":
    main()