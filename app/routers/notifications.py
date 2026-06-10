from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.database import get_db
from app.models import (
    Notification, Tournament, Match, Room,
    NotificationType, TournamentStatus
)
from app.schemas import (
    NotificationCreate, NotificationResponse,
    RoundStartNotification
)
from app.utils.scoring import calculate_tournament_rankings

router = APIRouter(prefix="/notifications", tags=["通知中心"])


@router.get("", response_model=List[NotificationResponse])
def list_notifications(
    tournament_id: Optional[int] = None,
    type: Optional[str] = None,
    round_number: Optional[int] = None,
    target_audience: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    query = db.query(Notification)
    if tournament_id:
        query = query.filter(Notification.tournament_id == tournament_id)
    if type:
        query = query.filter(Notification.type == type)
    if round_number:
        query = query.filter(Notification.round_number == round_number)
    if target_audience:
        query = query.filter(Notification.target_audience == target_audience)

    notifications = query.order_by(Notification.id.desc()).offset(skip).limit(limit).all()
    return notifications


@router.post("", response_model=NotificationResponse)
def create_notification(notification: NotificationCreate, db: Session = Depends(get_db)):
    if notification.tournament_id:
        tournament = db.query(Tournament).filter(
            Tournament.id == notification.tournament_id
        ).first()
        if not tournament:
            raise HTTPException(status_code=404, detail="赛事不存在")

    valid_types = [t.value for t in NotificationType]
    if notification.type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"无效的通知类型: {notification.type}，有效值为 {valid_types}"
        )

    db_notification = Notification(**notification.model_dump())
    db.add(db_notification)
    db.commit()
    db.refresh(db_notification)
    return db_notification


@router.get("/{notification_id}", response_model=NotificationResponse)
def get_notification(notification_id: int, db: Session = Depends(get_db)):
    notification = db.query(Notification).filter(
        Notification.id == notification_id
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="通知不存在")

    notification.read_count += 1
    db.commit()
    db.refresh(notification)
    return notification


@router.delete("/{notification_id}")
def delete_notification(notification_id: int, db: Session = Depends(get_db)):
    notification = db.query(Notification).filter(
        Notification.id == notification_id
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="通知不存在")

    db.delete(notification)
    db.commit()
    return {"message": "通知已删除"}


@router.post("/round-start")
def send_round_start_notification(
    request: RoundStartNotification,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(
        Tournament.id == request.tournament_id
    ).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    if tournament.status not in [TournamentStatus.ONGOING.value, TournamentStatus.READY.value]:
        raise HTTPException(
            status_code=400,
            detail=f"赛事状态为{tournament.status}，无法发送轮次开始通知"
        )

    if request.round_number < 1 or request.round_number > tournament.total_rounds:
        raise HTTPException(
            status_code=400,
            detail=f"轮次号必须在1到{tournament.total_rounds}之间"
        )

    matches = db.query(Match).filter(
        Match.tournament_id == request.tournament_id,
        Match.round_number == request.round_number
    ).all()

    room_details = []
    for match in matches:
        room = db.query(Room).filter(Room.id == match.room_id).first()
        room_details.append({
            "match_id": match.id,
            "room_id": room.id if room else None,
            "room_number": room.room_number if room else match.table_number,
            "room_name": room.name if room else f"{match.table_number}号桌",
            "table_number": match.table_number,
            "players_count": len(match.match_players),
        })

    total_players = sum(r["players_count"] for r in room_details)

    title = f"【第{request.round_number}轮开始】{tournament.name}"
    content_parts = [
        f"赛事：{tournament.name}",
        f"第 {request.round_number} / {tournament.total_rounds} 轮比赛开始！",
        f"本共 {len(matches)} 桌，{total_players} 名选手参赛",
        f"请各位选手尽快前往各自座位就座，祝大家好运！",
    ]
    if request.custom_message:
        content_parts.append(f"\n提示：{request.custom_message}")

    if tournament.location:
        content_parts.append(f"\n地点：{tournament.location}")

    content = "\n".join(content_parts)

    notification = Notification(
        tournament_id=request.tournament_id,
        type=NotificationType.ROUND_START.value,
        title=title,
        content=content,
        round_number=request.round_number,
        target_audience="all"
    )
    db.add(notification)
    db.flush()

    rankings = calculate_tournament_rankings(db, request.tournament_id, request.round_number - 1 if request.round_number > 1 else None)
    current_rankings = rankings[:5]

    db.commit()
    db.refresh(notification)

    return {
        "message": "轮次开始通知已发送",
        "notification_id": notification.id,
        "title": title,
        "content": content,
        "round": {
            "round_number": request.round_number,
            "total_rounds": tournament.total_rounds,
            "total_matches": len(matches),
            "total_players": total_players,
            "rooms": room_details,
        },
        "current_top5": [
            {
                "rank": r["rank"],
                "player_name": r["player_name"],
                "match_points": r["match_points"],
            }
            for r in current_rankings
        ],
    }


@router.post("/result-submitted/{match_id}")
def send_result_submitted_notification(
    match_id: int,
    submitted_by: str,
    db: Session = Depends(get_db)
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="对局不存在")

    tournament = match.tournament
    room = match.room

    winners = [mp for mp in match.match_players if mp.is_winner]
    winner_names = "、".join([mp.player.name if mp.player else "" for mp in winners])

    title = f"【成绩提交】第{match.round_number}轮 桌{match.table_number or room.room_number if room else match.id}"
    content_parts = [
        f"赛事：{tournament.name if tournament else ''}",
        f"对局：第{match.round_number}轮 第{match.table_number or room.room_number if room else ''}桌",
        f"提交人：{submitted_by}",
        f"胜者：{winner_names or '无'}",
    ]

    content_parts.append("\n各选手成绩：")
    for mp in sorted(match.match_players, key=lambda x: x.seat_position):
        player = mp.player
        name = player.name if player else ""
        flag = " 🏆" if mp.is_winner else ""
        content_parts.append(f"  座位{mp.seat_position}. {name}: {mp.score}分 [{mp.result}]{flag}")

    content = "\n".join(content_parts)

    notification = Notification(
        tournament_id=match.tournament_id,
        type=NotificationType.RESULT_SUBMITTED.value,
        title=title,
        content=content,
        round_number=match.round_number,
        target_audience="referees"
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)

    return {
        "message": "成绩提交通知已发送",
        "notification_id": notification.id,
        "title": title,
        "content": content,
    }


@router.get("/tournament/{tournament_id}/latest")
def get_latest_notifications(
    tournament_id: int,
    limit: int = Query(5, ge=1, le=50),
    include_types: Optional[str] = None,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    query = db.query(Notification).filter(
        (Notification.tournament_id == tournament_id) | (Notification.tournament_id.is_(None))
    )

    if include_types:
        types_list = include_types.split(",")
        query = query.filter(Notification.type.in_(types_list))

    notifications = query.order_by(Notification.id.desc()).limit(limit).all()

    result = []
    for n in notifications:
        result.append({
            "id": n.id,
            "type": n.type,
            "title": n.title,
            "content": n.content,
            "round_number": n.round_number,
            "target_audience": n.target_audience,
            "sent_at": n.sent_at,
            "is_new": (datetime.now() - n.sent_at).total_seconds() < 300
        })

    global_notifications = db.query(Notification).filter(
        Notification.tournament_id.is_(None)
    ).order_by(Notification.id.desc()).limit(3).all()

    return {
        "tournament_id": tournament_id,
        "tournament_name": tournament.name,
        "latest_count": len(result),
        "notifications": result,
        "global_announcements": [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "content": n.content,
                "sent_at": n.sent_at,
            }
            for n in global_notifications
        ],
    }


@router.post("/broadcast")
def send_broadcast_notification(
    tournament_id: Optional[int],
    title: str,
    content: str,
    target_audience: str = "all",
    type: str = "general",
    db: Session = Depends(get_db)
):
    valid_types = [t.value for t in NotificationType]
    if type not in valid_types:
        type = NotificationType.GENERAL.value

    notification = Notification(
        tournament_id=tournament_id,
        type=type,
        title=title,
        content=content,
        target_audience=target_audience
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)

    return {
        "message": "广播通知已发送",
        "notification_id": notification.id,
        "title": title,
        "target_audience": target_audience,
    }
