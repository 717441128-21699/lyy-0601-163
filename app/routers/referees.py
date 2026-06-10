from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from io import StringIO, BytesIO
import csv
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
    match.confirmed_by = f"裁判改判:{referee.name}"
    match.confirmed_at = datetime.now()
    if match.referee_note:
        match.referee_note += f" | 改判: {request.reason} (裁判:{referee.name})"
    else:
        match.referee_note = f"改判: {request.reason} (裁判:{referee.name})"

    db.flush()

    new_mp_list = db.query(MatchPlayer).filter(MatchPlayer.match_id == match_id).all()
    new_score_data = serialize_match_scores(new_mp_list)

    change_type = "score_override"
    if original_status in [MatchStatus.PENDING_CONFIRMATION.value, MatchStatus.REJECTED.value]:
        change_type = "confirm_override"
    elif original_status != MatchStatus.FINISHED.value:
        change_type = "score_set"

    change_log = ScoreChangeLog(
        match_id=match_id,
        referee_id=request.referee_id,
        change_type=change_type,
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


# ============================================================
# 【独立审计接口】 - 路径前缀: /referees/audit/*
# 专门用于查询裁判改判记录、操作审计
# 与裁判详情接口 /referees/{referee_id} 完全分离，避免参数混淆
# ============================================================

@router.get("/audit/score-changes")
def query_audit_score_changes(
    tournament_id: Optional[int] = Query(None, description="按赛事ID筛选"),
    referee_id: Optional[int] = Query(None, description="按裁判ID筛选"),
    match_id: Optional[int] = Query(None, description="按对局ID筛选"),
    change_type: Optional[str] = Query(None, description="改判类型: score_override / score_set"),
    start_date: Optional[datetime] = Query(None, description="起始时间 (>=)"),
    end_date: Optional[datetime] = Query(None, description="结束时间 (<=)"),
    skip: int = Query(0, ge=0, description="分页偏移"),
    limit: int = Query(200, ge=1, le=2000, description="每页数量"),
    include_details: bool = Query(True, description="是否包含新旧比分详细对比"),
    db: Session = Depends(get_db)
):
    """
    🔍 裁判改判记录查询 - 独立审计接口

    **筛选条件（可任意组合）：**
    - 按赛事筛选: tournament_id
    - 按裁判筛选: referee_id
    - 按对局筛选: match_id
    - 按改判类型: change_type
    - 按时间范围: start_date ~ end_date

    **接口独立说明：**
    - 本接口位于 /audit/score-changes 路径下
    - 与裁判详情接口 GET /referees/{referee_id} 完全独立
    - 不会因为把 audit 或其他字符串误当成 referee_id 传入而出错
    """
    query = db.query(ScoreChangeLog)

    if tournament_id:
        tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
        if not tournament:
            raise HTTPException(status_code=404, detail=f"赛事 tournament_id={tournament_id} 不存在")
        query = query.join(Match, ScoreChangeLog.match_id == Match.id).filter(
            Match.tournament_id == tournament_id
        )

    if referee_id:
        referee = db.query(Referee).filter(Referee.id == referee_id).first()
        if not referee:
            raise HTTPException(status_code=404, detail=f"裁判 referee_id={referee_id} 不存在")
        query = query.filter(ScoreChangeLog.referee_id == referee_id)

    if match_id:
        match = db.query(Match).filter(Match.id == match_id).first()
        if not match:
            raise HTTPException(status_code=404, detail=f"对局 match_id={match_id} 不存在")
        query = query.filter(ScoreChangeLog.match_id == match_id)

    if change_type:
        valid_types = ["score_override", "score_set"]
        if change_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"改判类型无效: {change_type}，有效值为: {valid_types}"
            )
        query = query.filter(ScoreChangeLog.change_type == change_type)

    if start_date:
        query = query.filter(ScoreChangeLog.created_at >= start_date)
    if end_date:
        query = query.filter(ScoreChangeLog.created_at <= end_date)

    total = query.count()
    logs = query.order_by(ScoreChangeLog.id.desc()).offset(skip).limit(limit).all()

    result_list = []
    for log in logs:
        referee = db.query(Referee).filter(Referee.id == log.referee_id).first() if log.referee_id else None
        match = db.query(Match).filter(Match.id == log.match_id).first()
        tournament_from_match = db.query(Tournament).filter(
            Tournament.id == match.tournament_id
        ).first() if match else None

        item = {
            "log_id": log.id,
            "change_type": log.change_type,
            "reason": log.reason,
            "created_at": log.created_at,
            "match": {
                "match_id": log.match_id,
                "tournament_id": match.tournament_id if match else None,
                "tournament_name": tournament_from_match.name if tournament_from_match else None,
                "round_number": match.round_number if match else None,
                "table_number": match.table_number if match else None,
                "room_id": match.room_id if match else None,
            } if match else None,
            "referee": {
                "referee_id": log.referee_id,
                "name": referee.name if referee else None,
                "username": referee.username if referee else None,
                "role": referee.role if referee else None,
            } if referee else {
                "referee_id": None,
                "note": "本记录无裁判关联（可能由系统自动操作）"
            },
        }

        if include_details:
            old_data = json.loads(log.old_score_data) if log.old_score_data else []
            new_data = json.loads(log.new_score_data) if log.new_score_data else []

            comparisons = []
            for idx in range(max(len(old_data), len(new_data))):
                old_item = old_data[idx] if idx < len(old_data) else None
                new_item = new_data[idx] if idx < len(new_data) else None
                if old_item and new_item:
                    changed_fields = []
                    if old_item.get("score") != new_item.get("score"):
                        changed_fields.append({
                            "field": "score",
                            "old": old_item.get("score"),
                            "new": new_item.get("score"),
                            "diff": new_item.get("score", 0) - old_item.get("score", 0)
                        })
                    if old_item.get("result") != new_item.get("result"):
                        changed_fields.append({
                            "field": "result",
                            "old": old_item.get("result"),
                            "new": new_item.get("result")
                        })
                    if old_item.get("is_winner") != new_item.get("is_winner"):
                        changed_fields.append({
                            "field": "is_winner",
                            "old": old_item.get("is_winner"),
                            "new": new_item.get("is_winner")
                        })
                    comparisons.append({
                        "player_id": new_item.get("player_id"),
                        "player_name": "",
                        "changed_fields": changed_fields,
                    })

            item["score_changes"] = {
                "old_scores": old_data,
                "new_scores": new_data,
                "differences": comparisons,
            }

        result_list.append(item)

    filters_applied = {
        "tournament_id": tournament_id,
        "referee_id": referee_id,
        "match_id": match_id,
        "change_type": change_type,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
    }

    return {
        "total_matched": total,
        "returned_count": len(result_list),
        "pagination": {
            "skip": skip,
            "limit": limit,
            "has_more": (skip + len(result_list)) < total
        },
        "filters": filters_applied,
        "changes": result_list,
    }


@router.get("/audit/tournaments/{tournament_id}/summary")
def get_tournament_audit_summary(
    tournament_id: int,
    group_by: str = Query("referee", pattern="^(referee|type|round)$"),
    db: Session = Depends(get_db)
):
    """
    📊 赛事改判统计汇总

    按不同维度统计某赛事的所有改判情况：
    - referee: 按裁判分组统计
    - type: 按改判类型分组统计
    - round: 按轮次分组统计

    路径完全独立，不会与裁判详情 /referees/{referee_id} 混淆
    """
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail=f"赛事 tournament_id={tournament_id} 不存在")

    matches = db.query(Match).filter(Match.tournament_id == tournament_id).all()
    match_ids = [m.id for m in matches]

    all_logs = db.query(ScoreChangeLog).filter(
        ScoreChangeLog.match_id.in_(match_ids)
    ).order_by(ScoreChangeLog.id.desc()).all() if match_ids else []

    total_changes = len(all_logs)
    unique_referees = set()
    affected_matches = set()

    log_items = []
    for log in all_logs:
        if log.referee_id:
            unique_referees.add(log.referee_id)
        affected_matches.add(log.match_id)

        referee = db.query(Referee).filter(Referee.id == log.referee_id).first() if log.referee_id else None
        match = db.query(Match).filter(Match.id == log.match_id).first()
        log_items.append({
            "log_id": log.id,
            "change_type": log.change_type,
            "round_number": match.round_number if match else None,
            "table_number": match.table_number if match else None,
            "referee_id": log.referee_id,
            "referee_name": referee.name if referee else None,
            "reason": log.reason,
            "created_at": log.created_at,
        })

    grouped_data = {}
    if group_by == "referee":
        for log in all_logs:
            rid = log.referee_id or 0
            key = str(rid)
            if key not in grouped_data:
                referee = db.query(Referee).filter(Referee.id == rid).first() if rid else None
                grouped_data[key] = {
                    "referee_id": rid,
                    "referee_name": referee.name if referee else "无裁判记录",
                    "count": 0,
                    "logs": []
                }
            grouped_data[key]["count"] += 1
            grouped_data[key]["logs"].append({
                "log_id": log.id,
                "reason": log.reason,
                "created_at": log.created_at,
            })
    elif group_by == "type":
        for log in all_logs:
            key = log.change_type
            if key not in grouped_data:
                grouped_data[key] = {"change_type": key, "count": 0, "logs": []}
            grouped_data[key]["count"] += 1
            grouped_data[key]["logs"].append({
                "log_id": log.id,
                "reason": log.reason,
                "created_at": log.created_at,
            })
    elif group_by == "round":
        for log in all_logs:
            match = db.query(Match).filter(Match.id == log.match_id).first()
            rn = match.round_number if match else 0
            key = str(rn)
            if key not in grouped_data:
                grouped_data[key] = {"round_number": rn, "count": 0, "logs": []}
            grouped_data[key]["count"] += 1
            grouped_data[key]["logs"].append({
                "log_id": log.id,
                "reason": log.reason,
                "change_type": log.change_type,
            })

    refs = db.query(TournamentReferee).filter(
        TournamentReferee.tournament_id == tournament_id
    ).all()
    assigned_referees = []
    for tr in refs:
        r = tr.referee
        count = sum(1 for l in all_logs if l.referee_id == r.id)
        assigned_referees.append({
            "referee_id": r.id,
            "name": r.name,
            "username": r.username,
            "assigned_role": tr.assigned_role or r.role,
            "changes_count": count,
            "assigned_at": tr.assigned_at,
        })

    return {
        "tournament": {
            "tournament_id": tournament_id,
            "name": tournament.name,
            "status": tournament.status,
            "total_rounds": tournament.total_rounds,
        },
        "summary": {
            "total_score_changes": total_changes,
            "unique_referees_involved": len(unique_referees),
            "matches_affected": len(affected_matches),
            "first_change_at": log_items[-1]["created_at"] if log_items else None,
            "last_change_at": log_items[0]["created_at"] if log_items else None,
        },
        "group_by": group_by,
        "grouped_statistics": list(grouped_data.values()),
        "assigned_referees": assigned_referees,
        "all_logs": log_items,
    }


@router.get("/audit/referees/{referee_id}/changes")
def get_referee_audit_history(
    referee_id: int,
    tournament_id: Optional[int] = Query(None, description="只看某赛事下的记录"),
    start_date: Optional[datetime] = Query(None, description="起始时间"),
    end_date: Optional[datetime] = Query(None, description="结束时间"),
    db: Session = Depends(get_db)
):
    """
    📋 单个裁判的改判历史

    路径: /audit/referees/{referee_id}/changes

    **与裁判详情接口区分：**
    - 裁判详情: GET /referees/{referee_id} → 返回裁判基本信息
    - 裁判改判历史: GET /referees/audit/referees/{referee_id}/changes → 返回改判记录列表

    两个接口路径完全不同，不会混淆参数。
    """
    referee = db.query(Referee).filter(Referee.id == referee_id).first()
    if not referee:
        raise HTTPException(status_code=404, detail=f"裁判 referee_id={referee_id} 不存在")

    query = db.query(ScoreChangeLog).filter(ScoreChangeLog.referee_id == referee_id)

    if tournament_id:
        query = query.join(Match, ScoreChangeLog.match_id == Match.id).filter(
            Match.tournament_id == tournament_id
        )
    if start_date:
        query = query.filter(ScoreChangeLog.created_at >= start_date)
    if end_date:
        query = query.filter(ScoreChangeLog.created_at <= end_date)

    logs = query.order_by(ScoreChangeLog.id.desc()).all()

    total = len(logs)
    by_type = {}
    tournament_stats = {}

    log_details = []
    for log in logs:
        t = log.change_type
        by_type[t] = by_type.get(t, 0) + 1

        match = db.query(Match).filter(Match.id == log.match_id).first()
        tid = match.tournament_id if match else None
        t_name = ""
        if tid:
            if tid not in tournament_stats:
                tournament = db.query(Tournament).filter(Tournament.id == tid).first()
                tournament_stats[tid] = {
                    "tournament_id": tid,
                    "tournament_name": tournament.name if tournament else "",
                    "count": 0
                }
            tournament_stats[tid]["count"] += 1
            t_name = tournament_stats[tid]["tournament_name"]

        old_data = json.loads(log.old_score_data) if log.old_score_data else []
        new_data = json.loads(log.new_score_data) if log.new_score_data else []

        log_details.append({
            "log_id": log.id,
            "change_type": log.change_type,
            "reason": log.reason,
            "created_at": log.created_at,
            "match_info": {
                "match_id": log.match_id,
                "tournament_id": tid,
                "tournament_name": t_name,
                "round_number": match.round_number if match else None,
                "table_number": match.table_number if match else None,
            } if match else None,
            "old_scores_count": len(old_data),
            "new_scores_count": len(new_data),
        })

    return {
        "referee": {
            "referee_id": referee.id,
            "name": referee.name,
            "username": referee.username,
            "role": referee.role,
            "is_active": referee.is_active,
        },
        "filters": {
            "tournament_id": tournament_id,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
        "statistics": {
            "total_changes": total,
            "by_change_type": by_type,
            "tournaments_involved": list(tournament_stats.values()),
        },
        "change_logs": log_details,
    }


# ============================================================
# 以下为兼容旧版本的保留接口（已标记不推荐）
# 建议使用上方独立的 /audit/* 系列接口
# ============================================================

@router.get("/change-logs", response_model=List[ScoreChangeLogResponse])
def list_change_logs_legacy(
    tournament_id: Optional[int] = None,
    referee_id: Optional[int] = None,
    change_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """
    ⚠️ [已过时] 请使用独立审计接口 GET /referees/audit/score-changes

    保留本接口仅用于兼容旧版调用方。
    新版推荐使用独立审计接口，避免与 /referees/{referee_id} 裁判详情路径混淆。
    """
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
def get_tournament_audit_log_legacy(
    tournament_id: int,
    db: Session = Depends(get_db)
):
    """
    ⚠️ [已过时] 请使用独立审计接口 GET /referees/audit/tournaments/{tournament_id}/summary

    保留本接口仅用于兼容旧版调用方。
    """
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
        "note": "⚠️ 本接口已标记为过时，请使用 GET /referees/audit/tournaments/{tournament_id}/summary",
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


def _build_audit_export_query(
    db: Session,
    tournament_id: Optional[int] = None,
    referee_id: Optional[int] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
):
    query = db.query(ScoreChangeLog)

    if tournament_id:
        tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
        if not tournament:
            raise HTTPException(status_code=404, detail=f"赛事 tournament_id={tournament_id} 不存在")
        query = query.join(Match, ScoreChangeLog.match_id == Match.id).filter(
            Match.tournament_id == tournament_id
        )

    if referee_id:
        referee = db.query(Referee).filter(Referee.id == referee_id).first()
        if not referee:
            raise HTTPException(status_code=404, detail=f"裁判 referee_id={referee_id} 不存在")
        query = query.filter(ScoreChangeLog.referee_id == referee_id)

    if start_date:
        query = query.filter(ScoreChangeLog.created_at >= start_date)
    if end_date:
        query = query.filter(ScoreChangeLog.created_at <= end_date)

    return query.order_by(ScoreChangeLog.id.desc())


def _format_log_for_export(log, db: Session) -> dict:
    referee = db.query(Referee).filter(Referee.id == log.referee_id).first() if log.referee_id else None
    match_obj = db.query(Match).filter(Match.id == log.match_id).first()

    old_data = json.loads(log.old_score_data) if log.old_score_data else []
    new_data = json.loads(log.new_score_data) if log.new_score_data else []

    old_scores_str = "; ".join(
        f"选手#{s.get('player_id', '?')}={s.get('score', 0)}({s.get('result', '?')})"
        for s in old_data
    ) if old_data else ""

    new_scores_str = "; ".join(
        f"选手#{s.get('player_id', '?')}={s.get('score', 0)}({s.get('result', '?')})"
        for s in new_data
    ) if new_data else ""

    return {
        "log_id": log.id,
        "match_id": log.match_id,
        "tournament_id": match_obj.tournament_id if match_obj else None,
        "round_number": match_obj.round_number if match_obj else None,
        "table_number": match_obj.table_number if match_obj else None,
        "change_type": log.change_type,
        "referee_id": log.referee_id,
        "referee_name": referee.name if referee else None,
        "referee_role": referee.role if referee else None,
        "old_scores": old_scores_str,
        "new_scores": new_scores_str,
        "old_scores_detail": old_data,
        "new_scores_detail": new_data,
        "reason": log.reason,
        "operation_time": log.created_at.isoformat() if log.created_at else None,
    }


@router.get("/audit/export")
def export_audit_records(
    format: str = Query("json", description="导出格式: json / csv"),
    tournament_id: Optional[int] = Query(None, description="按赛事ID筛选"),
    referee_id: Optional[int] = Query(None, description="按裁判ID筛选"),
    start_date: Optional[datetime] = Query(None, description="起始时间 (>=)"),
    end_date: Optional[datetime] = Query(None, description="结束时间 (<=)"),
    db: Session = Depends(get_db)
):
    query = _build_audit_export_query(db, tournament_id, referee_id, start_date, end_date)
    change_logs = query.all()

    if not change_logs:
        if format.lower() == "csv":
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(["log_id", "match_id", "tournament_id", "round_number", "table_number",
                             "change_type", "referee_id", "referee_name", "old_scores", "new_scores",
                             "reason", "operation_time"])
            output.seek(0)
            return StreamingResponse(
                iter([output.getvalue()]),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=audit_export.csv"}
            )
        return JSONResponse(
            content={"total_records": 0, "records": [], "message": "没有匹配的改判记录"},
            media_type="application/json"
        )

    records = [_format_log_for_export(log, db) for log in change_logs]

    if format.lower() == "csv":
        output = StringIO()
        if records:
            fieldnames = ["log_id", "match_id", "tournament_id", "round_number", "table_number",
                          "change_type", "referee_id", "referee_name", "old_scores", "new_scores",
                          "reason", "operation_time"]
            writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                writer.writerow(record)

        output.seek(0)
        byte_output = BytesIO()
        byte_output.write(output.getvalue().encode("utf-8-sig"))
        byte_output.seek(0)

        filename = f"audit_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(
            iter([byte_output.getvalue()]),
            media_type="text/csv; charset=utf-8-sig",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    filename = f"audit_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    export_data = {
        "export_time": datetime.now().isoformat(),
        "filters": {
            "tournament_id": tournament_id,
            "referee_id": referee_id,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
        "total_records": len(records),
        "records": records,
    }

    json_content = json.dumps(export_data, ensure_ascii=False, indent=2, default=str)
    return StreamingResponse(
        iter([json_content.encode("utf-8")]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
