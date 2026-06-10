from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime
import math

from app.database import get_db
from app.models import (
    Room, Tournament, Group, TournamentPlayer, Player,
    Match, MatchPlayer, MatchStatus, TournamentStatus
)
from app.schemas import (
    RoomCreate, RoomUpdate, RoomResponse,
    SeatAssignmentResponse, SeatAssignment
)
from app.utils.scoring import assign_seat_numbers, calculate_tournament_rankings, generate_swiss_pairings

router = APIRouter(prefix="/rooms", tags=["房间与座位"])


@router.get("", response_model=List[RoomResponse])
def list_rooms(
    tournament_id: Optional[int] = None,
    group_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Room)
    if tournament_id:
        query = query.filter(Room.tournament_id == tournament_id)
    if group_id:
        query = query.filter(Room.group_id == group_id)
    rooms = query.order_by(Room.room_number.asc()).all()
    return rooms


@router.post("", response_model=RoomResponse)
def create_room(room: RoomCreate, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == room.tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    if room.group_id:
        group = db.query(Group).filter(
            Group.id == room.group_id,
            Group.tournament_id == room.tournament_id
        ).first()
        if not group:
            raise HTTPException(status_code=404, detail="分组不存在")

    existing = db.query(Room).filter(
        Room.tournament_id == room.tournament_id,
        Room.room_number == room.room_number
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="房间号已存在")

    db_room = Room(**room.model_dump())
    db.add(db_room)
    db.commit()
    db.refresh(db_room)
    return db_room


@router.post("/batch", response_model=List[RoomResponse])
def create_rooms_batch(
    tournament_id: int,
    count: int = Query(..., ge=1, le=100),
    group_id: Optional[int] = None,
    capacity: int = Query(4, ge=2, le=10),
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    max_existing = db.query(Room).filter(
        Room.tournament_id == tournament_id
    ).count()

    created = []
    for i in range(count):
        room_num = max_existing + i + 1
        room = Room(
            tournament_id=tournament_id,
            group_id=group_id,
            name=f"{room_num}号桌",
            room_number=room_num,
            table_number=str(room_num),
            capacity=capacity
        )
        db.add(room)
        db.flush()
        created.append(room)
    db.commit()
    for r in created:
        db.refresh(r)
    return created


@router.get("/{room_id}", response_model=RoomResponse)
def get_room(room_id: int, db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    return room


@router.put("/{room_id}", response_model=RoomResponse)
def update_room(room_id: int, room_update: RoomUpdate, db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")

    update_data = room_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(room, key, value)

    db.commit()
    db.refresh(room)
    return room


@router.delete("/{room_id}")
def delete_room(room_id: int, db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")

    match_count = db.query(Match).filter(Match.room_id == room_id).count()
    if match_count > 0:
        raise HTTPException(status_code=400, detail="房间有对局记录，无法删除")

    db.delete(room)
    db.commit()
    return {"message": "房间已删除"}


@router.post("/{tournament_id}/generate-seats")
def generate_seat_assignments(
    tournament_id: int,
    method: str = Query("random", pattern="^(random|seed|rating)$"),
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    if tournament.status not in [TournamentStatus.DRAFT.value, TournamentStatus.REGISTRATION.value, TournamentStatus.READY.value]:
        raise HTTPException(status_code=400, detail="赛事状态不允许生座位表")

    assignments = assign_seat_numbers(db, tournament_id, method)

    for reg_id, seat_num in assignments.items():
        registration = db.query(TournamentPlayer).filter(
            TournamentPlayer.id == reg_id
        ).first()
        if registration:
            registration.seat_number = seat_num

    db.commit()

    return {
        "message": "座位表已生成",
        "method": method,
        "total_assigned": len(assignments),
        "assignments": assignments
    }


@router.get("/{tournament_id}/seating-chart")
def get_seating_chart(
    tournament_id: int,
    group_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    registrations_query = db.query(TournamentPlayer).filter(
        TournamentPlayer.tournament_id == tournament_id,
        TournamentPlayer.is_substitute == False
    )
    if group_id:
        registrations_query = registrations_query.filter(TournamentPlayer.group_id == group_id)

    registrations = registrations_query.order_by(
        TournamentPlayer.seat_number.asc().nullslast()
    ).all()

    rooms = db.query(Room).filter(Room.tournament_id == tournament_id).order_by(Room.room_number).all()
    if not rooms:
        rooms_count = math.ceil(len(registrations) / tournament.players_per_room) if registrations else 0
    else:
        rooms_count = len(rooms)

    seating_chart: List[Dict[str, Any]] = []
    players_per_room = tournament.players_per_room

    for room_idx in range(rooms_count):
        room = rooms[room_idx] if room_idx < len(rooms) else None
        start_idx = room_idx * players_per_room
        end_idx = start_idx + players_per_room
        room_regs = registrations[start_idx:end_idx]

        assignments = []
        for pos_idx, reg in enumerate(room_regs):
            player = reg.player
            assignments.append(SeatAssignment(
                room_id=room.id if room else 0,
                seat_position=pos_idx + 1,
                tournament_player_id=reg.id,
                player_id=reg.player_id,
                player_name=player.name if player else "Unknown"
            ))

        seating_chart.append({
            "room": {
                "id": room.id if room else None,
                "room_number": room.room_number if room else room_idx + 1,
                "name": room.name if room else f"{room_idx + 1}号桌",
                "table_number": room.table_number if room else None,
                "capacity": room.capacity if room else players_per_room,
            },
            "assignments": assignments
        })

    return {
        "tournament_id": tournament_id,
        "tournament_name": tournament.name,
        "total_rooms": len(seating_chart),
        "total_players": len(registrations),
        "seating_chart": seating_chart
    }


@router.get("/{tournament_id}/round/{round_number}/assignments")
def get_round_assignments(
    tournament_id: int,
    round_number: int,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    matches = db.query(Match).filter(
        Match.tournament_id == tournament_id,
        Match.round_number == round_number
    ).all()

    result = []
    for match in matches:
        room = match.room
        match_players = db.query(MatchPlayer).filter(
            MatchPlayer.match_id == match.id
        ).order_by(MatchPlayer.seat_position).all()

        players_info = []
        for mp in match_players:
            player = mp.player
            players_info.append({
                "match_player_id": mp.id,
                "tournament_player_id": mp.tournament_player_id,
                "player_id": mp.player_id,
                "player_name": player.name if player else "",
                "team": player.team if player else None,
                "seat_position": mp.seat_position,
                "start_rank": mp.start_rank,
            })

        result.append({
            "match_id": match.id,
            "room_id": room.id if room else None,
            "room_number": room.room_number if room else match.table_number,
            "room_name": room.name if room else f"桌{match.table_number}",
            "table_number": match.table_number,
            "match_status": match.status,
            "players": players_info,
        })

    return {
        "tournament_id": tournament_id,
        "round_number": round_number,
        "total_matches": len(result),
        "assignments": result
    }


@router.post("/{tournament_id}/round/{round_number}/generate-pairings")
def generate_round_pairings(
    tournament_id: int,
    round_number: int,
    force: bool = False,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    existing_matches = db.query(Match).filter(
        Match.tournament_id == tournament_id,
        Match.round_number == round_number
    ).count()

    if existing_matches > 0 and not force:
        raise HTTPException(status_code=400, detail=f"第{round_number}轮对局已存在，如需重新生成请使用force=true")

    if existing_matches > 0 and force:
        old_matches = db.query(Match).filter(
            Match.tournament_id == tournament_id,
            Match.round_number == round_number
        ).all()
        for m in old_matches:
            db.query(MatchPlayer).filter(MatchPlayer.match_id == m.id).delete()
            db.delete(m)
        db.flush()

    rankings = calculate_tournament_rankings(db, tournament_id, round_number - 1 if round_number > 1 else None)

    active_reg_ids = [r["tournament_player_id"] for r in rankings]

    pairings = generate_swiss_pairings(rankings, round_number, tournament.players_per_room)

    rooms = db.query(Room).filter(Room.tournament_id == tournament_id).order_by(Room.room_number).all()
    if not rooms:
        rooms_count = len(pairings)
        rooms = []
        for i in range(rooms_count):
            r = Room(
                tournament_id=tournament_id,
                name=f"{i + 1}号桌",
                room_number=i + 1,
                table_number=str(i + 1),
                capacity=tournament.players_per_room
            )
            db.add(r)
            db.flush()
            rooms.append(r)

    created_matches = []
    for table_idx, table in enumerate(pairings):
        room = rooms[table_idx] if table_idx < len(rooms) else None
        match = Match(
            tournament_id=tournament_id,
            room_id=room.id if room else None,
            round_number=round_number,
            table_number=table_idx + 1,
            status=MatchStatus.PENDING.value
        )
        db.add(match)
        db.flush()

        rank_map = {r["tournament_player_id"]: r["rank"] for r in rankings}
        for pos_idx, reg_id in enumerate(table):
            reg = db.query(TournamentPlayer).filter(TournamentPlayer.id == reg_id).first()
            if reg:
                mp = MatchPlayer(
                    match_id=match.id,
                    tournament_player_id=reg_id,
                    player_id=reg.player_id,
                    seat_position=pos_idx + 1,
                    start_rank=rank_map.get(reg_id)
                )
                db.add(mp)

        created_matches.append({
            "match_id": match.id,
            "room_id": room.id if room else None,
            "room_number": room.room_number if room else table_idx + 1,
            "table_number": table_idx + 1,
            "player_count": len(table),
        })

    db.commit()

    return {
        "message": f"第{round_number}轮对阵已生成",
        "round_number": round_number,
        "total_matches": len(created_matches),
        "total_players": sum(m["player_count"] for m in created_matches),
        "matches": created_matches
    }
