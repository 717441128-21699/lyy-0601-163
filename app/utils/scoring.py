from typing import List, Dict, Tuple, Optional
from sqlalchemy.orm import Session
from app.models import (
    Tournament, TournamentPlayer, Match, MatchPlayer, Foul,
    MatchStatus, ResultType
)
from collections import defaultdict
import hashlib
import json
from datetime import datetime


def calculate_opponent_match_win_percent(
    tournament_player_id: int,
    all_mp_data: Dict[int, Dict],
    opponents_map: Dict[int, List[int]]
) -> float:
    opponent_ids = opponents_map.get(tournament_player_id, [])
    if not opponent_ids:
        return 0.0
    total_matches = 0
    total_wins = 0
    for opp_id in opponent_ids:
        data = all_mp_data.get(opp_id)
        if data:
            total_matches += data["played_rounds"]
            total_wins += data["wins"]
    if total_matches == 0:
        return 0.0
    return total_wins / total_matches if total_matches > 0 else 0.0


def calculate_game_win_percent(
    tournament_player_id: int,
    match_players_list: List[MatchPlayer]
) -> float:
    player_matches = [mp for mp in match_players_list if mp.tournament_player_id == tournament_player_id]
    total_games = sum(mp.score for mp in player_matches)
    if not player_matches:
        return 0.0
    max_possible = len(player_matches) * 3.0
    if max_possible == 0:
        return 0.0
    return total_games / max_possible


def calculate_opponent_game_win_percent(
    tournament_player_id: int,
    game_win_percents: Dict[int, float],
    opponents_map: Dict[int, List[int]]
) -> float:
    opponent_ids = opponents_map.get(tournament_player_id, [])
    if not opponent_ids:
        return 0.0
    total_ogwp = sum(game_win_percents.get(opp_id, 0.0) for opp_id in opponent_ids)
    return total_ogwp / len(opponent_ids) if opponent_ids else 0.0


def calculate_tournament_rankings(
    db: Session,
    tournament_id: int,
    round_number: Optional[int] = None,
    group_id: Optional[int] = None
) -> List[Dict]:
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        return []

    registrations_query = db.query(TournamentPlayer).filter(
        TournamentPlayer.tournament_id == tournament_id
    )
    if group_id:
        registrations_query = registrations_query.filter(TournamentPlayer.group_id == group_id)
    registrations = registrations_query.all()

    matches_query = db.query(Match).filter(
        Match.tournament_id == tournament_id,
        Match.status.in_([MatchStatus.FINISHED.value, MatchStatus.OVERRIDDEN.value])
    )
    if round_number:
        matches_query = matches_query.filter(Match.round_number <= round_number)
    matches = matches_query.all()

    match_ids = [m.id for m in matches]
    match_players = db.query(MatchPlayer).filter(MatchPlayer.match_id.in_(match_ids)).all() if match_ids else []

    fouls = db.query(Foul).filter(
        Foul.tournament_id == tournament_id,
        Foul.approved == True
    ).all()

    mp_stats: Dict[int, Dict] = {}
    opponents_map: Dict[int, List[int]] = defaultdict(list)
    match_to_players: Dict[int, List[int]] = defaultdict(list)

    for mp in match_players:
        match_to_players[mp.match_id].append(mp.tournament_player_id)

    for match_id, player_ids in match_to_players.items():
        for pid in player_ids:
            for opp_id in player_ids:
                if opp_id != pid:
                    opponents_map[pid].append(opp_id)

    for reg in registrations:
        pid = reg.id
        mp_stats[pid] = {
            "tournament_player_id": pid,
            "player_id": reg.player_id,
            "player_name": reg.player.name if reg.player else "",
            "team": reg.player.team if reg.player else None,
            "played_rounds": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "match_points": 0.0,
            "total_tiebreaker": 0.0,
        }

    for mp in match_players:
        pid = mp.tournament_player_id
        if pid not in mp_stats:
            continue
        mp_stats[pid]["played_rounds"] += 1
        mp_stats[pid]["total_tiebreaker"] += mp.tiebreaker_score

        if mp.result == ResultType.WIN.value:
            mp_stats[pid]["wins"] += 1
            mp_stats[pid]["match_points"] += tournament.win_points
        elif mp.result == ResultType.DRAW.value:
            mp_stats[pid]["draws"] += 1
            mp_stats[pid]["match_points"] += tournament.draw_points
        elif mp.result == ResultType.LOSE.value:
            mp_stats[pid]["losses"] += 1
            mp_stats[pid]["match_points"] += tournament.lose_points

    game_win_percents: Dict[int, float] = {}
    for pid in mp_stats:
        game_win_percents[pid] = calculate_game_win_percent(pid, match_players)

    rankings = []
    for pid, stats in mp_stats.items():
        omwp = calculate_opponent_match_win_percent(pid, mp_stats, opponents_map)
        gwp = game_win_percents.get(pid, 0.0)
        ogwp = calculate_opponent_game_win_percent(pid, game_win_percents, opponents_map)

        foul_penalty = sum(
            f.penalty_points for f in fouls
            if f.player_id == stats["player_id"]
        )

        ranking_item = {
            **stats,
            "opponent_match_win_percent": round(omwp, 4),
            "game_win_percent": round(gwp, 4),
            "opponent_game_win_percent": round(ogwp, 4),
            "foul_penalty": foul_penalty,
            "match_points": round(stats["match_points"] - foul_penalty, 2),
        }
        rankings.append(ranking_item)

    rankings.sort(
        key=lambda x: (
            -x["match_points"],
            -x["opponent_match_win_percent"],
            -x["game_win_percent"],
            -x["opponent_game_win_percent"],
            -x["total_tiebreaker"],
        )
    )

    for idx, item in enumerate(rankings):
        item["rank"] = idx + 1

    return rankings


def generate_submission_hash(
    match_id: int,
    scores: List[Dict]
) -> str:
    data = {
        "match_id": match_id,
        "scores": sorted(scores, key=lambda x: x.get("match_player_id", 0)),
        "timestamp": datetime.now().isoformat()
    }
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_duplicate_submission(
    db: Session,
    match_id: int,
    submission_hash: str
) -> bool:
    if not submission_hash:
        return False
    existing = db.query(Match).filter(
        Match.id == match_id,
        Match.submission_hash == submission_hash
    ).first()
    if existing:
        return True
    other_hash = db.query(Match).filter(
        Match.submission_hash == submission_hash
    ).first()
    return other_hash is not None


def generate_swiss_pairings(
    rankings: List[Dict],
    round_number: int,
    players_per_match: int = 4
) -> List[List[int]]:
    if not rankings:
        return []

    available = [r["tournament_player_id"] for r in rankings]
    pairings: List[List[int]] = []

    if len(available) % players_per_match != 0:
        while len(available) % players_per_match != 0:
            available.append(None)

    i = 0
    while i < len(available):
        table = available[i:i + players_per_match]
        if all(p is not None for p in table):
            pairings.append(table)
        elif any(p is not None for p in table):
            valid_players = [p for p in table if p is not None]
            if valid_players:
                pairings.append(valid_players)
        i += players_per_match

    return pairings


def assign_seat_numbers(
    db: Session,
    tournament_id: int,
    method: str = "random"
) -> Dict[int, int]:
    registrations = db.query(TournamentPlayer).filter(
        TournamentPlayer.tournament_id == tournament_id,
        TournamentPlayer.is_substitute == False
    ).all()

    assignments: Dict[int, int] = {}

    if method == "seed":
        sorted_regs = sorted(
            registrations,
            key=lambda r: (r.seed if r.seed else 9999, r.id)
        )
    elif method == "rating":
        sorted_regs = sorted(
            registrations,
            key=lambda r: -(r.player.rating if r.player else 1000)
        )
    else:
        import random
        sorted_regs = list(registrations)
        random.shuffle(sorted_regs)

    for idx, reg in enumerate(sorted_regs):
        assignments[reg.id] = idx + 1

    return assignments


def serialize_match_scores(match_players: List[MatchPlayer]) -> str:
    data = []
    for mp in match_players:
        data.append({
            "match_player_id": mp.id,
            "player_id": mp.player_id,
            "score": mp.score,
            "result": mp.result,
            "tiebreaker": mp.tiebreaker_score,
            "is_winner": mp.is_winner,
        })
    return json.dumps(data, ensure_ascii=False, default=str)


def get_player_match_history(
    db: Session,
    player_id: int,
    limit: int = 20
) -> List[Dict]:
    match_players = db.query(MatchPlayer).filter(
        MatchPlayer.player_id == player_id
    ).order_by(MatchPlayer.id.desc()).limit(limit).all()

    history = []
    for mp in match_players:
        match = db.query(Match).filter(Match.id == mp.match_id).first()
        tournament = match.tournament if match else None

        opponents = db.query(MatchPlayer).filter(
            MatchPlayer.match_id == mp.match_id,
            MatchPlayer.player_id != player_id
        ).all()

        history.append({
            "match_id": mp.match_id,
            "tournament_id": match.tournament_id if match else None,
            "tournament_name": tournament.name if tournament else "",
            "round_number": match.round_number if match else None,
            "score": mp.score,
            "result": mp.result,
            "tiebreaker_score": mp.tiebreaker_score,
            "opponents": [
                {
                    "player_id": opp.player_id,
                    "player_name": opp.player.name if opp.player else "",
                    "score": opp.score,
                    "result": opp.result,
                }
                for opp in opponents
            ],
            "played_at": match.end_time if match and match.end_time else match.created_at,
        })

    return history
