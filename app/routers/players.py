from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.database import get_db
from app.models import Player, PlayerStatus, TournamentPlayer, Tournament, MatchPlayer, Match
from app.schemas import (
    PlayerCreate, PlayerUpdate, PlayerResponse,
    PlayerHistoryResponse
)
from app.utils.scoring import get_player_match_history

router = APIRouter(prefix="/players", tags=["选手管理"])


@router.get("", response_model=List[PlayerResponse])
def list_players(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    status: Optional[str] = None,
    team: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Player)
    if status:
        query = query.filter(Player.status == status)
    if team:
        query = query.filter(Player.team == team)
    if search:
        query = query.filter(
            (Player.name.contains(search)) |
            (Player.phone.contains(search)) |
            (Player.id_card.contains(search))
        )
    players = query.order_by(Player.id.desc()).offset(skip).limit(limit).all()
    return players


@router.post("", response_model=PlayerResponse)
def create_player(player: PlayerCreate, db: Session = Depends(get_db)):
    if player.id_card:
        existing = db.query(Player).filter(Player.id_card == player.id_card).first()
        if existing:
            raise HTTPException(status_code=400, detail="该身份证号已注册")

    db_player = Player(**player.model_dump())
    db.add(db_player)
    db.commit()
    db.refresh(db_player)
    return db_player


@router.get("/{player_id}", response_model=PlayerResponse)
def get_player(player_id: int, db: Session = Depends(get_db)):
    player = db.query(Player).filter(Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="选手不存在")
    return player


@router.put("/{player_id}", response_model=PlayerResponse)
def update_player(player_id: int, player_update: PlayerUpdate, db: Session = Depends(get_db)):
    player = db.query(Player).filter(Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="选手不存在")

    update_data = player_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(player, key, value)

    db.commit()
    db.refresh(player)
    return player


@router.delete("/{player_id}")
def delete_player(player_id: int, db: Session = Depends(get_db)):
    player = db.query(Player).filter(Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="选手不存在")

    registrations = db.query(TournamentPlayer).filter(
        TournamentPlayer.player_id == player_id
    ).count()
    if registrations > 0:
        raise HTTPException(status_code=400, detail="该选手有赛事报名记录，无法删除")

    db.delete(player)
    db.commit()
    return {"message": "选手已删除"}


@router.post("/batch", response_model=List[PlayerResponse])
def create_players_batch(players: List[PlayerCreate], db: Session = Depends(get_db)):
    created = []
    for player_data in players:
        if player_data.id_card:
            existing = db.query(Player).filter(Player.id_card == player_data.id_card).first()
            if existing:
                created.append(existing)
                continue
        db_player = Player(**player_data.model_dump())
        db.add(db_player)
        db.flush()
        created.append(db_player)
    db.commit()
    for p in created:
        db.refresh(p)
    return created


@router.get("/{player_id}/history", response_model=PlayerHistoryResponse)
def get_player_history(
    player_id: int,
    tournament_id: Optional[int] = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    player = db.query(Player).filter(Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="选手不存在")

    match_players_query = db.query(MatchPlayer).filter(MatchPlayer.player_id == player_id)
    total_matches = match_players_query.count()
    total_wins = match_players_query.filter(MatchPlayer.result == "win").count()
    total_losses = match_players_query.filter(MatchPlayer.result == "lose").count()
    total_draws = match_players_query.filter(MatchPlayer.result == "draw").count()

    win_rate = (total_wins / total_matches * 100) if total_matches > 0 else 0.0

    registrations = db.query(TournamentPlayer).filter(
        TournamentPlayer.player_id == player_id
    ).all()

    tournaments_info = []
    for reg in registrations:
        tournament = reg.tournament
        if tournament_id and tournament.id != tournament_id:
            continue
        tourney_mps = db.query(MatchPlayer).join(Match).filter(
            MatchPlayer.tournament_player_id == reg.id,
            Match.status.in_(["finished", "overridden"])
        ).all()
        tw = sum(1 for mp in tourney_mps if mp.result == "win")
        tl = sum(1 for mp in tourney_mps if mp.result == "lose")
        td = sum(1 for mp in tourney_mps if mp.result == "draw")
        tpoints = sum(mp.score for mp in tourney_mps)
        tournaments_info.append({
            "tournament_id": tournament.id,
            "tournament_name": tournament.name,
            "game_type": tournament.game_type,
            "final_rank": reg.final_rank,
            "matches_played": len(tourney_mps),
            "wins": tw,
            "losses": tl,
            "draws": td,
            "total_points": tpoints,
            "drop_round": reg.drop_round,
        })

    recent_matches = get_player_match_history(db, player_id, limit)
    if tournament_id:
        recent_matches = [m for m in recent_matches if m["tournament_id"] == tournament_id]

    return PlayerHistoryResponse(
        player_id=player_id,
        player_name=player.name,
        total_matches=total_matches,
        total_wins=total_wins,
        total_losses=total_losses,
        total_draws=total_draws,
        win_rate=round(win_rate, 2),
        tournaments=tournaments_info,
        recent_matches=recent_matches
    )
