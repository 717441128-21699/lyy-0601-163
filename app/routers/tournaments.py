from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.database import get_db
from app.models import (
    Tournament, TournamentStatus, Group, TournamentPlayer,
    Player, Referee, TournamentReferee
)
from app.schemas import (
    TournamentCreate, TournamentUpdate, TournamentResponse,
    GroupCreate, GroupResponse, GroupWithPlayersResponse,
    RegistrationCreate, RegistrationUpdate, RegistrationResponse
)

router = APIRouter(prefix="/tournaments", tags=["赛事与分组"])


@router.get("", response_model=List[TournamentResponse])
def list_tournaments(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    status: Optional[str] = None,
    game_type: Optional[str] = None,
    is_test: Optional[bool] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Tournament)
    if status:
        query = query.filter(Tournament.status == status)
    if game_type:
        query = query.filter(Tournament.game_type == game_type)
    if is_test is not None:
        query = query.filter(Tournament.is_test == is_test)
    if search:
        query = query.filter(
            (Tournament.name.contains(search)) |
            (Tournament.organizer.contains(search))
        )
    tournaments = query.order_by(Tournament.id.desc()).offset(skip).limit(limit).all()
    return tournaments


@router.post("", response_model=TournamentResponse)
def create_tournament(tournament: TournamentCreate, db: Session = Depends(get_db)):
    db_tournament = Tournament(**tournament.model_dump())
    db.add(db_tournament)
    db.commit()
    db.refresh(db_tournament)
    return db_tournament


@router.get("/{tournament_id}", response_model=TournamentResponse)
def get_tournament(tournament_id: int, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")
    return tournament


@router.put("/{tournament_id}", response_model=TournamentResponse)
def update_tournament(
    tournament_id: int,
    tournament_update: TournamentUpdate,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    update_data = tournament_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tournament, key, value)

    db.commit()
    db.refresh(tournament)
    return tournament


@router.delete("/{tournament_id}")
def delete_tournament(tournament_id: int, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    if tournament.status not in [TournamentStatus.DRAFT.value, TournamentStatus.CANCELLED.value]:
        raise HTTPException(status_code=400, detail="赛事进行中，无法删除")

    db.delete(tournament)
    db.commit()
    return {"message": "赛事已删除"}


@router.post("/{tournament_id}/start")
def start_tournament(tournament_id: int, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    reg_count = db.query(TournamentPlayer).filter(
        TournamentPlayer.tournament_id == tournament_id,
        TournamentPlayer.is_substitute == False
    ).count()

    if reg_count < tournament.players_per_room:
        raise HTTPException(status_code=400, detail=f"报名人数不足，至少需要{tournament.players_per_room}人")

    if not tournament.rooms:
        raise HTTPException(status_code=400, detail="请先创建房间")

    tournament.status = TournamentStatus.ONGOING.value
    if not tournament.start_date:
        tournament.start_date = datetime.now()
    db.commit()

    return {"message": "赛事已开始", "tournament_status": tournament.status}


@router.post("/{tournament_id}/finish")
def finish_tournament(tournament_id: int, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    pending_matches = db.query(Tournament.matches).filter(
        Tournament.id == tournament_id
    ).join(Tournament.matches).filter(
        ~Tournament.matches.property.mapper.class_.status.in_(["finished", "cancelled", "overridden"])
    ).count()

    if pending_matches > 0:
        raise HTTPException(status_code=400, detail=f"还有{pending_matches}场对局未完成")

    tournament.status = TournamentStatus.FINISHED.value
    tournament.end_date = datetime.now()
    db.commit()

    return {"message": "赛事已结束", "tournament_status": tournament.status}


@router.post("/groups", response_model=GroupResponse)
def create_group(group: GroupCreate, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == group.tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    db_group = Group(**group.model_dump())
    db.add(db_group)
    db.commit()
    db.refresh(db_group)
    return db_group


@router.get("/{tournament_id}/groups", response_model=List[GroupWithPlayersResponse])
def list_tournament_groups(tournament_id: int, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    groups = db.query(Group).filter(Group.tournament_id == tournament_id).all()
    result = []
    for g in groups:
        players = db.query(TournamentPlayer).filter(
            TournamentPlayer.group_id == g.id
        ).all()
        player_info = [
            {
                "id": p.id,
                "player_id": p.player_id,
                "player_name": p.player.name if p.player else "",
                "seed": p.seed,
                "seat_number": p.seat_number,
                "checked_in": p.checked_in,
            }
            for p in players
        ]
        result.append(GroupWithPlayersResponse(
            id=g.id,
            tournament_id=g.tournament_id,
            name=g.name,
            description=g.description,
            players=player_info
        ))
    return result


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db)):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="分组不存在")

    player_count = db.query(TournamentPlayer).filter(
        TournamentPlayer.group_id == group_id
    ).count()
    if player_count > 0:
        raise HTTPException(status_code=400, detail="分组下还有选手，无法删除")

    db.delete(group)
    db.commit()
    return {"message": "分组已删除"}


@router.post("/{tournament_id}/registrations", response_model=RegistrationResponse)
def register_player(registration: RegistrationCreate, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == registration.tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    player = db.query(Player).filter(Player.id == registration.player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="选手不存在")

    existing = db.query(TournamentPlayer).filter(
        TournamentPlayer.tournament_id == registration.tournament_id,
        TournamentPlayer.player_id == registration.player_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该选手已报名此赛事")

    if tournament.status not in [TournamentStatus.DRAFT.value, TournamentStatus.REGISTRATION.value]:
        raise HTTPException(status_code=400, detail="赛事已关闭报名")

    current_count = db.query(TournamentPlayer).filter(
        TournamentPlayer.tournament_id == registration.tournament_id,
        TournamentPlayer.is_substitute == False
    ).count()
    if not registration.is_substitute and current_count >= tournament.max_players:
        raise HTTPException(status_code=400, detail="赛事报名人数已满")

    if registration.group_id:
        group = db.query(Group).filter(
            Group.id == registration.group_id,
            Group.tournament_id == registration.tournament_id
        ).first()
        if not group:
            raise HTTPException(status_code=404, detail="分组不存在")

    db_reg = TournamentPlayer(**registration.model_dump())
    db.add(db_reg)
    db.commit()
    db.refresh(db_reg)

    return RegistrationResponse(
        id=db_reg.id,
        tournament_id=db_reg.tournament_id,
        player_id=db_reg.player_id,
        group_id=db_reg.group_id,
        seed=db_reg.seed,
        seat_number=db_reg.seat_number,
        registration_time=db_reg.registration_time,
        checked_in=db_reg.checked_in,
        checkin_time=db_reg.checkin_time,
        is_substitute=db_reg.is_substitute,
        final_rank=db_reg.final_rank,
        drop_round=db_reg.drop_round,
        player={
            "id": player.id,
            "name": player.name,
            "team": player.team,
            "avatar": player.avatar,
        }
    )


@router.get("/{tournament_id}/registrations", response_model=List[RegistrationResponse])
def list_registrations(
    tournament_id: int,
    group_id: Optional[int] = None,
    is_substitute: Optional[bool] = None,
    checked_in: Optional[bool] = None,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    query = db.query(TournamentPlayer).filter(TournamentPlayer.tournament_id == tournament_id)
    if group_id:
        query = query.filter(TournamentPlayer.group_id == group_id)
    if is_substitute is not None:
        query = query.filter(TournamentPlayer.is_substitute == is_substitute)
    if checked_in is not None:
        query = query.filter(TournamentPlayer.checked_in == checked_in)

    registrations = query.order_by(TournamentPlayer.seed.asc().nullslast(), TournamentPlayer.id.asc()).all()

    result = []
    for reg in registrations:
        player = reg.player
        result.append(RegistrationResponse(
            id=reg.id,
            tournament_id=reg.tournament_id,
            player_id=reg.player_id,
            group_id=reg.group_id,
            seed=reg.seed,
            seat_number=reg.seat_number,
            registration_time=reg.registration_time,
            checked_in=reg.checked_in,
            checkin_time=reg.checkin_time,
            is_substitute=reg.is_substitute,
            final_rank=reg.final_rank,
            drop_round=reg.drop_round,
            player={
                "id": player.id,
                "name": player.name,
                "team": player.team,
                "avatar": player.avatar,
                "rating": player.rating,
            } if player else None
        ))
    return result


@router.put("/registrations/{registration_id}", response_model=RegistrationResponse)
def update_registration(
    registration_id: int,
    update: RegistrationUpdate,
    db: Session = Depends(get_db)
):
    registration = db.query(TournamentPlayer).filter(TournamentPlayer.id == registration_id).first()
    if not registration:
        raise HTTPException(status_code=404, detail="报名记录不存在")

    update_data = update.model_dump(exclude_unset=True)
    if "checked_in" in update_data and update_data["checked_in"] and not registration.checked_in:
        update_data["checkin_time"] = datetime.now()

    for key, value in update_data.items():
        setattr(registration, key, value)

    db.commit()
    db.refresh(registration)

    player = registration.player
    return RegistrationResponse(
        id=registration.id,
        tournament_id=registration.tournament_id,
        player_id=registration.player_id,
        group_id=registration.group_id,
        seed=registration.seed,
        seat_number=registration.seat_number,
        registration_time=registration.registration_time,
        checked_in=registration.checked_in,
        checkin_time=registration.checkin_time,
        is_substitute=registration.is_substitute,
        final_rank=registration.final_rank,
        drop_round=registration.drop_round,
        player={
            "id": player.id,
            "name": player.name,
            "team": player.team,
            "avatar": player.avatar,
        } if player else None
    )


@router.delete("/registrations/{registration_id}")
def cancel_registration(registration_id: int, db: Session = Depends(get_db)):
    registration = db.query(TournamentPlayer).filter(TournamentPlayer.id == registration_id).first()
    if not registration:
        raise HTTPException(status_code=404, detail="报名记录不存在")

    tournament = registration.tournament
    if tournament and tournament.status in [TournamentStatus.ONGOING.value, TournamentStatus.FINISHED.value]:
        raise HTTPException(status_code=400, detail="赛事进行中，无法取消报名。请使用退赛功能。")

    db.delete(registration)
    db.commit()
    return {"message": "已取消报名"}


@router.post("/{tournament_id}/referees/{referee_id}")
def assign_referee_to_tournament(
    tournament_id: int,
    referee_id: int,
    assigned_role: Optional[str] = None,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    referee = db.query(Referee).filter(Referee.id == referee_id).first()
    if not referee:
        raise HTTPException(status_code=404, detail="裁判不存在")

    existing = db.query(TournamentReferee).filter(
        TournamentReferee.tournament_id == tournament_id,
        TournamentReferee.referee_id == referee_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该裁判已分配到本赛事")

    assignment = TournamentReferee(
        tournament_id=tournament_id,
        referee_id=referee_id,
        assigned_role=assigned_role
    )
    db.add(assignment)
    db.commit()

    return {"message": "裁判已分配", "assignment_id": assignment.id}


@router.delete("/{tournament_id}/referees/{referee_id}")
def remove_referee_from_tournament(
    tournament_id: int,
    referee_id: int,
    db: Session = Depends(get_db)
):
    assignment = db.query(TournamentReferee).filter(
        TournamentReferee.tournament_id == tournament_id,
        TournamentReferee.referee_id == referee_id
    ).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="分配记录不存在")

    db.delete(assignment)
    db.commit()
    return {"message": "裁判已移除"}
