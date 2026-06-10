from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import json
import hashlib

from app.database import get_db
from app.models import (
    Referee, TournamentReferee, ScoreChangeLog, Match, MatchPlayer,
    MatchStatus, ResultType, RefereeRole, Tournament
)
from app.schemas import (
    RefereeCreate, RefereeLogin, RefereeResponse,
    ScoreOverrideRequest, ScoreChangeLogResponse
)
from app.utils.scoring import serialize_match_scores

router = APIRouter(prefix="/referees", tags=["裁判与改判"])


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


@router.get("", response_model=List[RefereeResponse])
def list_referees(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Referee)
    if role:
        query = query.filter(Referee.role == role)
    if is_active is not None:
        query = query.filter(Referee.is_active == is_active)
    referees = query.order_by(Referee.id.desc()).offset(skip).limit(limit).all()
    return referees


@router.post("", response_model=RefereeResponse)
def create_referee(referee: RefereeCreate, db: Session = Depends(get_db)):
    existing = db.query(Referee).filter(Referee.username == referee.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")

    password_hash = _hash_password(referee.password)

    db_referee = Referee(
        name=referee.name,
        avatar=referee.avatar,
        phone=referee.phone,
        username=referee.username,
        password_hash=password_hash,
        role=referee.role,
        is_active=referee.is_active
    )
    db.add(db_referee)
    db.commit()
    db.refresh(db_referee)
    return db_referee


@router.post("/login")
def referee_login(login: RefereeLogin, db: Session = Depends(get_db)):
    referee = db.query(Referee).filter(Referee.username == login.username).first()
    if not referee:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not referee.is_active:
        raise HTTPException(status_code=403, detail="该裁判账号已被停用")

    password_hash = _hash_password(login.password)
    if password_hash != referee.password_hash:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    return {
        "message": "登录成功",
        "referee_id": referee.id,
        "name": referee.name,
        "username": referee.username,
        "role": referee.role,
        "token": f"referee_{referee.id}_{int(datetime.now().timestamp())}"
    }


@router.get("/{referee_id}", response_model=RefereeResponse)
def get_referee(referee_id: int, db: Session = Depends(get_db)):
    referee = db.query(Referee).filter(Referee.id == referee_id).first()
    if not referee:
        raise HTTPException(status_code=404, detail="裁判不存在")
    return referee


@router.put("/{referee_id}", response_model=RefereeResponse)
def update_referee(
    referee_id: int,
    referee_update: dict,
    db: Session = Depends(get_db)
):
    referee = db.query(Referee).filter(Referee.id == referee_id).first()
    if not referee:
        raise HTTPException(status_code=404, detail="裁判不存在")

    allowed_fields = ["name", "avatar", "phone", "role", "is_active"]
    for key, value in referee_update.items():
        if key in allowed_fields:
            setattr(referee, key, value)
        if key == "password" and value:
            referee.password_hash = _hash_password(value)

    db.commit()
    db.refresh(referee)
    return referee


@router.delete("/{referee_id}")
def delete_referee(referee_id: int, db: Session = Depends(get_db)):
    referee = db.query(Referee).filter(Referee.id == referee_id).first()
    if not referee:
        raise HTTPException(status_code=404, detail="裁判不存在")

    log_count = db.query(ScoreChangeLog).filter(
        ScoreChangeLog.referee_id == referee_id
    ).count()
    if log_count > 0:
        referee.is_active = False
        db.commit()
        return {"message": "裁判已停用（保留操作记录）", "action": "deactivated"}

    db.delete(referee)
    db.commit()
    return {"message": "裁判已删除", "action": "deleted"}


@router.post("/matches/{match_id}/override")
def override_match_score(
    match_id: int,
    request: ScoreOverrideRequest,
    db: Session = Depends(get_db)
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="对局不存在")

    referee = db.query(Referee).filter(Referee.id == request.referee_id).first()
    if not referee:
        raise HTTPException(status_code=404, detail="裁判不存在")
    if not referee.is_active:
        raise HTTPException(status_code=403, detail="该裁判账号已被停用")

    valid_results = [r.value for r in ResultType]
    for s in request.changes:
        if s.result not in valid_results:
            raise HTTPException(
                status_code=400,
                detail=f"无效的比赛结果: {s.result}，有效值为 {valid_results}"
            )

    old_mp_list = db.query(MatchPlayer).filter(MatchPlayer.match_id == match_id).all()
    old_score_data = serialize_match_scores(old_mp_list)

    valid_mp_ids = {mp.id for mp in old_mp_list}
    submitted_ids = {s.match_player_id for s in request.changes}
    missing_ids = valid_mp_ids - submitted_ids
    extra_ids = submitted_ids - valid_mp_ids

    if missing_ids:
        raise HTTPException(status_code=400, detail=f"缺少选手成绩: {list(missing_ids)}")
    if extra_ids:
        raise HTTPException(status_code=400, detail=f"无效的对局选手ID: {list(extra_ids)}")

    for s in request.changes:
        mp = db.query(MatchPlayer).filter(MatchPlayer.id == s.match_player_id).first()
        if mp:
            mp.score = s.score
            mp.result = s.result
            mp.tiebreaker_score = s.tiebreaker_score or 0.0
            mp.is_winner = (s.result == ResultType.WIN.value)

    original_status = match.status
    match.status = MatchStatus.OVERRIDDEN.value
    if match.referee_note:
        match.referee_note += f" | 改判: {request.reason} (裁判:{referee.name})"
    else:
        match.referee_note = f"改判: {request.reason} (裁判:{referee.name})"

    db.flush()

    new_mp_list = db.query(MatchPlayer).filter(MatchPlayer.match_id == match_id).all()
    new_score_data = serialize_match_scores(new_mp_list)

    change_log = ScoreChangeLog(
        match_id=match_id,
        referee_id=request.referee_id,
        change_type="score_override" if original_status == MatchStatus.FINISHED.value else "score_set",
        old_score_data=old_score_data,
        new_score_data=new_score_data,
        reason=request.reason
    )
    db.add(change_log)

    db.commit()
    db.refresh(match)

    return {
        "message": "比分已改判",
        "match_id": match_id,
        "original_status": original_status,
        "new_status": match.status,
        "referee": {
            "id": referee.id,
            "name": referee.name,
            "role": referee.role
        },
        "change_log_id": change_log.id,
        "reason": request.reason,
    }


@router.get("/matches/{match_id}/change-logs", response_model=List[ScoreChangeLogResponse])
def get_match_change_logs(
    match_id: int,
    db: Session = Depends(get_db)
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="对局不存在")

    logs = db.query(ScoreChangeLog).filter(
        ScoreChangeLog.match_id == match_id
    ).order_by(ScoreChangeLog.id.desc()).all()

    return logs


@router.get("/change-logs", response_model=List[ScoreChangeLogResponse])
def list_change_logs(
    tournament_id: Optional[int] = None,
    referee_id: Optional[int] = None,
    change_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    query = db.query(ScoreChangeLog)

    if tournament_id:
        query = query.join(Match, ScoreChangeLog.match_id == Match.id).filter(
            Match.tournament_id == tournament_id
        )
    if referee_id:
        query = query.filter(ScoreChangeLog.referee_id == referee_id)
    if change_type:
        query = query.filter(ScoreChangeLog.change_type == change_type)
    if start_date:
        query = query.filter(ScoreChangeLog.created_at >= start_date)
    if end_date:
        query = query.filter(ScoreChangeLog.created_at <= end_date)

    logs = query.order_by(ScoreChangeLog.id.desc()).offset(skip).limit(limit).all()
    return logs


@router.get("/tournaments/{tournament_id}/audit-log")
def get_tournament_audit_log(
    tournament_id: int,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    matches = db.query(Match).filter(Match.tournament_id == tournament_id).all()
    match_ids = [m.id for m in matches]

    change_logs = db.query(ScoreChangeLog).filter(
        ScoreChangeLog.match_id.in_(match_ids)
    ).order_by(ScoreChangeLog.id.desc()).all()

    audit_detail = []
    for log in change_logs:
        referee = db.query(Referee).filter(Referee.id == log.referee_id).first() if log.referee_id else None
        match = db.query(Match).filter(Match.id == log.match_id).first()

        old_data = json.loads(log.old_score_data) if log.old_score_data else []
        new_data = json.loads(log.new_score_data) if log.new_score_data else []

        audit_detail.append({
            "log_id": log.id,
            "match_id": log.match_id,
            "round_number": match.round_number if match else None,
            "room_id": match.room_id if match else None,
            "change_type": log.change_type,
            "referee_id": log.referee_id,
            "referee_name": referee.name if referee else None,
            "referee_role": referee.role if referee else None,
            "reason": log.reason,
            "created_at": log.created_at,
            "old_scores": old_data,
            "new_scores": new_data,
        })

    refs = db.query(TournamentReferee).filter(
        TournamentReferee.tournament_id == tournament_id
    ).all()
    assigned_referees = []
    for tr in refs:
        r = tr.referee
        assigned_referees.append({
            "referee_id": r.id,
            "name": r.name,
            "username": r.username,
            "role": tr.assigned_role or r.role,
            "assigned_at": tr.assigned_at,
        })

    return {
        "tournament_id": tournament_id,
        "tournament_name": tournament.name,
        "total_changes": len(change_logs),
        "assigned_referees": assigned_referees,
        "changes_by_type": {
            t: sum(1 for l in change_logs if l.change_type == t)
            for t in set(l.change_type for l in change_logs)
        },
        "audit_logs": audit_detail,
    }
