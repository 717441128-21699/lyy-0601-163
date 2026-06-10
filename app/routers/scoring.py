from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.database import get_db
from app.models import (
    Foul, Tournament, Player, Match, MatchPlayer,
    TournamentPlayer, MatchStatus, ResultType
)
from app.schemas import (
    FoulCreate, FoulUpdate, FoulResponse
)

router = APIRouter(prefix="/scoring", tags=["计分与犯规"])


@router.post("/fouls", response_model=FoulResponse)
def record_foul(foul: FoulCreate, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == foul.tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    player = db.query(Player).filter(Player.id == foul.player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="选手不存在")

    if foul.match_id:
        match = db.query(Match).filter(Match.id == foul.match_id).first()
        if not match:
            raise HTTPException(status_code=404, detail="对局不存在")
        if match.tournament_id != foul.tournament_id:
            raise HTTPException(status_code=400, detail="对局不属于此赛事")

        in_match = db.query(MatchPlayer).filter(
            MatchPlayer.match_id == foul.match_id,
            MatchPlayer.player_id == foul.player_id
        ).first()
        if not in_match:
            raise HTTPException(status_code=400, detail="该选手不在此对局中")

    db_foul = Foul(**foul.model_dump())
    db.add(db_foul)
    db.commit()
    db.refresh(db_foul)
    return db_foul


@router.get("/fouls", response_model=List[FoulResponse])
def list_fouls(
    tournament_id: Optional[int] = None,
    match_id: Optional[int] = None,
    player_id: Optional[int] = None,
    approved: Optional[bool] = None,
    type: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    query = db.query(Foul)
    if tournament_id:
        query = query.filter(Foul.tournament_id == tournament_id)
    if match_id:
        query = query.filter(Foul.match_id == match_id)
    if player_id:
        query = query.filter(Foul.player_id == player_id)
    if approved is not None:
        query = query.filter(Foul.approved == approved)
    if type:
        query = query.filter(Foul.type == type)

    fouls = query.order_by(Foul.id.desc()).offset(skip).limit(limit).all()
    return fouls


@router.put("/fouls/{foul_id}", response_model=FoulResponse)
def update_foul(
    foul_id: int,
    update: FoulUpdate,
    db: Session = Depends(get_db)
):
    foul = db.query(Foul).filter(Foul.id == foul_id).first()
    if not foul:
        raise HTTPException(status_code=404, detail="犯规记录不存在")

    update_data = update.model_dump(exclude_unset=True)
    if "approved" in update_data and update_data["approved"] and not foul.approved:
        update_data["approved_at"] = datetime.now()

    for key, value in update_data.items():
        setattr(foul, key, value)

    db.commit()
    db.refresh(foul)
    return foul


@router.delete("/fouls/{foul_id}")
def delete_foul(foul_id: int, db: Session = Depends(get_db)):
    foul = db.query(Foul).filter(Foul.id == foul_id).first()
    if not foul:
        raise HTTPException(status_code=404, detail="犯规记录不存在")

    if foul.approved:
        raise HTTPException(status_code=400, detail="已批准的犯规记录无法删除")

    db.delete(foul)
    db.commit()
    return {"message": "犯规记录已删除"}


@router.get("/{tournament_id}/player-foul-summary")
def get_player_foul_summary(
    tournament_id: int,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    fouls = db.query(Foul).filter(
        Foul.tournament_id == tournament_id,
        Foul.approved == True
    ).all()

    summary: Dict[int, Dict[str, Any]] = {}
    for foul in fouls:
        pid = foul.player_id
        if pid not in summary:
            player = db.query(Player).filter(Player.id == pid).first()
            summary[pid] = {
                "player_id": pid,
                "player_name": player.name if player else "",
                "team": player.team if player else None,
                "foul_count": 0,
                "total_penalty_points": 0.0,
                "total_penalty_rank": 0,
                "foul_details": [],
            }
        summary[pid]["foul_count"] += 1
        summary[pid]["total_penalty_points"] += foul.penalty_points
        summary[pid]["total_penalty_rank"] += foul.penalty_rank
        summary[pid]["foul_details"].append({
            "foul_id": foul.id,
            "type": foul.type,
            "description": foul.description,
            "match_id": foul.match_id,
            "penalty_points": foul.penalty_points,
            "penalty_rank": foul.penalty_rank,
            "reported_by": foul.reported_by,
            "reported_at": foul.reported_at,
        })

    result = sorted(
        list(summary.values()),
        key=lambda x: -x["total_penalty_points"]
    )

    return {
        "tournament_id": tournament_id,
        "total_fouls": len(fouls),
        "players_with_fouls": len(result),
        "players": result
    }


@router.get("/match/{match_id}/h2h")
def get_match_head_to_head(
    match_id: int,
    db: Session = Depends(get_db)
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="对局不存在")

    match_players = db.query(MatchPlayer).filter(
        MatchPlayer.match_id == match_id
    ).order_by(MatchPlayer.seat_position).all()

    player_ids = [mp.player_id for mp in match_players]

    past_h2h = {}
    for i, pid1 in enumerate(player_ids):
        for j, pid2 in enumerate(player_ids):
            if i >= j:
                continue
            key = f"{min(pid1, pid2)}_{max(pid1, pid2)}"
            if key in past_h2h:
                continue

            past_matches = db.query(MatchPlayer).join(Match).filter(
                Match.tournament_id == match.tournament_id,
                Match.id != match_id,
                Match.status.in_([MatchStatus.FINISHED.value, MatchStatus.OVERRIDDEN.value])
            ).filter(
                MatchPlayer.match_id.in_(
                    db.query(MatchPlayer.match_id).filter(
                        MatchPlayer.player_id.in_([pid1, pid2])
                    ).group_by(MatchPlayer.match_id).having(
                        db.func.count(db.func.distinct(MatchPlayer.player_id)) == 2
                    )
                )
            ).filter(
                MatchPlayer.player_id.in_([pid1, pid2])
            ).all()

            p1_wins = sum(
                1 for mp in past_matches
                if mp.player_id == pid1 and mp.result == ResultType.WIN.value
            )
            p2_wins = sum(
                1 for mp in past_matches
                if mp.player_id == pid2 and mp.result == ResultType.WIN.value
            )
            draws = sum(
                1 for mp in past_matches
                if mp.player_id == pid1 and mp.result == ResultType.DRAW.value
            )

            p1 = db.query(Player).filter(Player.id == pid1).first()
            p2 = db.query(Player).filter(Player.id == pid2).first()

            past_h2h[key] = {
                "player1_id": pid1,
                "player1_name": p1.name if p1 else "",
                "player1_wins": p1_wins,
                "player2_id": pid2,
                "player2_name": p2.name if p2 else "",
                "player2_wins": p2_wins,
                "draws": draws,
                "total_matches": p1_wins + p2_wins + draws,
            }

    current_match = []
    for mp in match_players:
        p = mp.player
        current_match.append({
            "match_player_id": mp.id,
            "player_id": mp.player_id,
            "player_name": p.name if p else "",
            "team": p.team if p else None,
            "seat_position": mp.seat_position,
            "score": mp.score,
            "result": mp.result,
            "tiebreaker_score": mp.tiebreaker_score,
            "is_winner": mp.is_winner,
        })

    return {
        "match_id": match_id,
        "tournament_id": match.tournament_id,
        "round_number": match.round_number,
        "match_status": match.status,
        "current_match": current_match,
        "head_to_head_history": list(past_h2h.values()),
    }


@router.post("/{tournament_id}/recalculate-ranks")
def recalculate_final_ranks(
    tournament_id: int,
    db: Session = Depends(get_db)
):
    from app.utils.scoring import calculate_tournament_rankings

    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    rankings = calculate_tournament_rankings(db, tournament_id)

    updated = 0
    for item in rankings:
        reg = db.query(TournamentPlayer).filter(
            TournamentPlayer.id == item["tournament_player_id"]
        ).first()
        if reg:
            reg.final_rank = item["rank"]
            updated += 1

    db.commit()

    return {
        "message": "最终名次已重新计算",
        "tournament_id": tournament_id,
        "players_updated": updated,
        "top_10": rankings[:10]
    }
