from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.database import get_db
from app.models import (
    Tournament, TournamentPlayer, Match, MatchPlayer,
    Player, Substitution, MatchStatus, ResultType, TournamentStatus
)
from app.schemas import (
    RankingResponse, RankingItem, BigScreenRankingResponse,
    SubstitutionCreate, SubstitutionResponse
)
from app.utils.scoring import calculate_tournament_rankings

router = APIRouter(prefix="/rankings", tags=["榜单与排名"])


@router.get("/{tournament_id}/current", response_model=RankingResponse)
def get_current_rankings(
    tournament_id: int,
    round_number: Optional[int] = None,
    group_id: Optional[int] = None,
    include_dropped: bool = Query(False),
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    rankings_data = calculate_tournament_rankings(db, tournament_id, round_number, group_id)

    if not include_dropped:
        dropped_ids = set()
        regs = db.query(TournamentPlayer).filter(
            TournamentPlayer.tournament_id == tournament_id,
            TournamentPlayer.drop_round.isnot(None)
        ).all()
        current_round = round_number if round_number else (
            db.query(Match.round_number).filter(
                Match.tournament_id == tournament_id
            ).order_by(Match.round_number.desc()).first()
        )
        current_round = current_round[0] if isinstance(current_round, tuple) and current_round else (
            tournament.total_rounds
        )
        for r in regs:
            if r.drop_round and r.drop_round <= current_round:
                dropped_ids.add(r.id)
        rankings_data = [r for r in rankings_data if r["tournament_player_id"] not in dropped_ids]

    for idx, item in enumerate(rankings_data):
        item["rank"] = idx + 1

    items = []
    for r in rankings_data:
        reg = db.query(TournamentPlayer).filter(
            TournamentPlayer.id == r["tournament_player_id"]
        ).first()
        is_dropped = reg.drop_round is not None if reg else False

        items.append(RankingItem(
            rank=r["rank"],
            tournament_player_id=r["tournament_player_id"],
            player_id=r["player_id"],
            player_name=r["player_name"],
            team=r.get("team"),
            played_rounds=r["played_rounds"],
            wins=r["wins"],
            draws=r["draws"],
            losses=r["losses"],
            match_points=r["match_points"],
            opponent_match_win_percent=r["opponent_match_win_percent"],
            game_win_percent=r["game_win_percent"],
            opponent_game_win_percent=r["opponent_game_win_percent"],
            total_tiebreaker=r["total_tiebreaker"],
            foul_penalty=r["foul_penalty"]
        ))

    return RankingResponse(
        tournament_id=tournament_id,
        round_number=round_number or tournament.total_rounds,
        group_id=group_id,
        rankings=items
    )


@router.get("/{tournament_id}/big-screen", response_model=BigScreenRankingResponse)
def get_big_screen_rankings(
    tournament_id: int,
    top_n: int = Query(20, ge=5, le=100),
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    latest_round = db.query(Match.round_number).filter(
        Match.tournament_id == tournament_id
    ).order_by(Match.round_number.desc()).first()
    current_round = latest_round[0] if latest_round else 0

    rankings_data = calculate_tournament_rankings(db, tournament_id, current_round)

    dropped_ids = set()
    regs = db.query(TournamentPlayer).filter(
        TournamentPlayer.tournament_id == tournament_id,
        TournamentPlayer.drop_round.isnot(None)
    ).all()
    for r in regs:
        if r.drop_round and r.drop_round <= current_round:
            dropped_ids.add(r.id)
    rankings_data = [r for r in rankings_data if r["tournament_player_id"] not in dropped_ids]

    for idx, item in enumerate(rankings_data):
        item["rank"] = idx + 1

    top_rankings = rankings_data[:top_n]

    rankings_display = []
    for r in top_rankings:
        reg = db.query(TournamentPlayer).filter(
            TournamentPlayer.id == r["tournament_player_id"]
        ).first()
        player = db.query(Player).filter(Player.id == r["player_id"]).first()
        trend = "stable"
        if reg and reg.final_rank:
            trend = "up" if r["rank"] < reg.final_rank else "down" if r["rank"] > reg.final_rank else "stable"

        rankings_display.append({
            "rank": r["rank"],
            "player_id": r["player_id"],
            "player_name": r["player_name"],
            "team": r.get("team"),
            "avatar": player.avatar if player else None,
            "match_points": r["match_points"],
            "played": r["played_rounds"],
            "wins": r["wins"],
            "draws": r["draws"],
            "losses": r["losses"],
            "tiebreaker": round(r["opponent_match_win_percent"] * 100, 1),
            "trend": trend,
        })

    highlights = []
    if len(top_rankings) >= 3:
        medals = ["🥇", "🥈", "🥉"]
        for i in range(min(3, len(top_rankings))):
            r = top_rankings[i]
            player = db.query(Player).filter(Player.id == r["player_id"]).first()
            highlights.append({
                "medal": medals[i],
                "rank": r["rank"],
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "team": r.get("team"),
                "avatar": player.avatar if player else None,
                "match_points": r["match_points"],
                "win_rate": round(r["wins"] / r["played_rounds"] * 100, 1) if r["played_rounds"] > 0 else 0,
            })

    current_round_matches = db.query(Match).filter(
        Match.tournament_id == tournament_id,
        Match.round_number == current_round,
        Match.status.in_([MatchStatus.ONGOING.value, MatchStatus.LOCKED.value])
    ).count()

    completed_matches = db.query(Match).filter(
        Match.tournament_id == tournament_id,
        Match.status.in_([MatchStatus.FINISHED.value, MatchStatus.OVERRIDDEN.value])
    ).count()

    return BigScreenRankingResponse(
        tournament_name=tournament.name,
        round_number=current_round,
        total_rounds=tournament.total_rounds,
        tournament_status=tournament.status,
        updated_at=datetime.now(),
        rankings=rankings_display,
        top_highlights=highlights,
        current_round_matches=current_round_matches,
        completed_matches=completed_matches
    )


@router.get("/{tournament_id}/cutline")
def get_cutline_info(
    tournament_id: int,
    cutoff_rank: Optional[int] = None,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    latest_round = db.query(Match.round_number).filter(
        Match.tournament_id == tournament_id
    ).order_by(Match.round_number.desc()).first()
    current_round = latest_round[0] if latest_round else 0

    rankings_data = calculate_tournament_rankings(db, tournament_id, current_round)

    dropped_ids = set()
    regs = db.query(TournamentPlayer).filter(
        TournamentPlayer.tournament_id == tournament_id,
        TournamentPlayer.drop_round.isnot(None)
    ).all()
    for r in regs:
        if r.drop_round and r.drop_round <= current_round:
            dropped_ids.add(r.id)
    rankings_data = [r for r in rankings_data if r["tournament_player_id"] not in dropped_ids]

    for idx, item in enumerate(rankings_data):
        item["rank"] = idx + 1

    if not cutoff_rank:
        total_players = len(rankings_data)
        cutoff_rank = max(1, int(total_players * 0.5))

    if cutoff_rank > len(rankings_data):
        cutoff_rank = len(rankings_data)

    above_cutoff = rankings_data[:cutoff_rank]
    at_cutoff = rankings_data[cutoff_rank - 1] if cutoff_rank > 0 else None
    below_cutoff = rankings_data[cutoff_rank:cutoff_rank + 5]

    threshold_points = at_cutoff["match_points"] if at_cutoff else 0

    near_cutoff = []
    for r in rankings_data:
        if abs(r["match_points"] - threshold_points) <= 3.0:
            near_cutoff.append({
                "rank": r["rank"],
                "player_name": r["player_name"],
                "match_points": r["match_points"],
                "diff": round(r["match_points"] - threshold_points, 2),
            })

    return {
        "tournament_id": tournament_id,
        "tournament_name": tournament.name,
        "current_round": current_round,
        "total_rounds": tournament.total_rounds,
        "cutoff_rank": cutoff_rank,
        "threshold_points": threshold_points,
        "total_qualified": len(above_cutoff),
        "cutoff_player": {
            "rank": at_cutoff["rank"],
            "player_id": at_cutoff["player_id"],
            "player_name": at_cutoff["player_name"],
            "match_points": at_cutoff["match_points"],
            "omwp": at_cutoff["opponent_match_win_percent"],
        } if at_cutoff else None,
        "top_qualified": [
            {
                "rank": r["rank"],
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "match_points": r["match_points"],
            }
            for r in above_cutoff[:10]
        ],
        "players_below_threatening": [
            {
                "rank": r["rank"],
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "match_points": r["match_points"],
                "points_needed": round(threshold_points - r["match_points"] + 0.01, 2),
            }
            for r in below_cutoff
        ],
        "near_cutoff_zone": near_cutoff,
    }


@router.post("/substitutions", response_model=SubstitutionResponse)
def process_substitution(substitution: SubstitutionCreate, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == substitution.tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    if substitution.old_player_id:
        old_player = db.query(Player).filter(Player.id == substitution.old_player_id).first()
        if not old_player:
            raise HTTPException(status_code=404, detail="被替换选手不存在")

        old_reg = db.query(TournamentPlayer).filter(
            TournamentPlayer.tournament_id == substitution.tournament_id,
            TournamentPlayer.player_id == substitution.old_player_id
        ).first()
        if not old_reg:
            raise HTTPException(status_code=400, detail="被替换选手未报名此赛事")

        if substitution.effective_round <= 1:
            pass
        else:
            check_round = substitution.effective_round - 1
            played = db.query(MatchPlayer).join(Match).filter(
                MatchPlayer.tournament_player_id == old_reg.id,
                Match.round_number < substitution.effective_round,
                Match.status.in_([MatchStatus.FINISHED.value, MatchStatus.OVERRIDDEN.value])
            ).count()
            if played > 0:
                old_reg.drop_round = substitution.effective_round

    new_player = db.query(Player).filter(Player.id == substitution.new_player_id).first()
    if not new_player:
        raise HTTPException(status_code=404, detail="替补选手不存在")

    new_reg = db.query(TournamentPlayer).filter(
        TournamentPlayer.tournament_id == substitution.tournament_id,
        TournamentPlayer.player_id == substitution.new_player_id
    ).first()

    if not new_reg:
        new_reg = TournamentPlayer(
            tournament_id=substitution.tournament_id,
            player_id=substitution.new_player_id,
            is_substitute=True,
            checked_in=True,
            checkin_time=datetime.now()
        )
        db.add(new_reg)
        db.flush()
    else:
        new_reg.is_substitute = False
        if substitution.old_player_id:
            old_reg = db.query(TournamentPlayer).filter(
                TournamentPlayer.tournament_id == substitution.tournament_id,
                TournamentPlayer.player_id == substitution.old_player_id
            ).first()
            if old_reg:
                new_reg.group_id = old_reg.group_id
                new_reg.seat_number = old_reg.seat_number
                new_reg.seed = old_reg.seed

    if substitution.old_player_id:
        future_match_players = db.query(MatchPlayer).join(Match).filter(
            MatchPlayer.player_id == substitution.old_player_id,
            Match.tournament_id == substitution.tournament_id,
            Match.round_number >= substitution.effective_round,
            Match.status.in_([MatchStatus.PENDING.value, MatchStatus.LOCKED.value])
        ).all()

        for fmp in future_match_players:
            fmp.player_id = substitution.new_player_id
            fmp.tournament_player_id = new_reg.id

    db_sub = Substitution(**substitution.model_dump())
    db.add(db_sub)
    db.commit()
    db.refresh(db_sub)

    return db_sub


@router.get("/substitutions", response_model=List[SubstitutionResponse])
def list_substitutions(
    tournament_id: Optional[int] = None,
    effective_round: Optional[int] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Substitution)
    if tournament_id:
        query = query.filter(Substitution.tournament_id == tournament_id)
    if effective_round:
        query = query.filter(Substitution.effective_round == effective_round)

    substitutions = query.order_by(Substitution.id.desc()).all()
    return substitutions


@router.post("/players/{registration_id}/drop")
def drop_player_from_tournament(
    registration_id: int,
    drop_round: int,
    reason: Optional[str] = None,
    approved_by: Optional[str] = None,
    db: Session = Depends(get_db)
):
    registration = db.query(TournamentPlayer).filter(
        TournamentPlayer.id == registration_id
    ).first()
    if not registration:
        raise HTTPException(status_code=404, detail="报名记录不存在")

    if registration.drop_round is not None:
        raise HTTPException(status_code=400, detail="该选手已退赛")

    registration.drop_round = drop_round

    future_matches = db.query(MatchPlayer).join(Match).filter(
        MatchPlayer.tournament_player_id == registration_id,
        Match.round_number >= drop_round,
        Match.status == MatchStatus.PENDING.value
    ).all()

    updated = 0
    for fmp in future_matches:
        fmp.result = ResultType.LOSE.value
        fmp.score = 0.0
        fmp.is_winner = False
        updated += 1

    tournament = registration.tournament
    sub = Substitution(
        tournament_id=registration.tournament_id,
        old_player_id=registration.player_id,
        new_player_id=registration.player_id,
        reason=reason or "选手主动退赛",
        effective_round=drop_round,
        approved_by=approved_by
    )
    db.add(sub)

    db.commit()

    return {
        "message": "选手已退赛",
        "registration_id": registration_id,
        "player_id": registration.player_id,
        "player_name": registration.player.name if registration.player else "",
        "drop_round": drop_round,
        "future_matches_updated": updated,
    }


@router.post("/players/{registration_id}/reinstate")
def reinstate_player(
    registration_id: int,
    effective_round: Optional[int] = None,
    reason: Optional[str] = None,
    db: Session = Depends(get_db)
):
    registration = db.query(TournamentPlayer).filter(
        TournamentPlayer.id == registration_id
    ).first()
    if not registration:
        raise HTTPException(status_code=404, detail="报名记录不存在")

    if registration.drop_round is None:
        raise HTTPException(status_code=400, detail="该选手未退赛")

    old_drop_round = registration.drop_round
    registration.drop_round = None

    if effective_round:
        affected_matches = db.query(MatchPlayer).join(Match).filter(
            MatchPlayer.tournament_player_id == registration_id,
            Match.round_number >= max(old_drop_round, 1),
            Match.round_number < effective_round,
            Match.status == MatchStatus.PENDING.value
        ).all()
        for mp in affected_matches:
            mp.result = None
            mp.score = 0.0
            mp.is_winner = False

    db.commit()

    return {
        "message": "选手已恢复参赛资格",
        "registration_id": registration_id,
        "original_drop_round": old_drop_round,
        "effective_round": effective_round,
    }
