from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import json

from app.database import get_db
from app.models import (
    Match, MatchPlayer, Tournament, Room, TournamentPlayer,
    MatchStatus, ResultType, TournamentStatus
)
from app.schemas import (
    MatchCreate, MatchResponse, MatchResultSubmit,
    MatchPlayerResponse
)
from app.utils.scoring import (
    generate_submission_hash, check_duplicate_submission,
    serialize_match_scores
)

router = APIRouter(prefix="/matches", tags=["对局管理"])


def _build_match_response(match: Match, db: Session) -> MatchResponse:
    match_players = db.query(MatchPlayer).filter(
        MatchPlayer.match_id == match.id
    ).order_by(MatchPlayer.seat_position).all()

    player_responses = []
    for mp in match_players:
        player = mp.player
        player_responses.append(MatchPlayerResponse(
            id=mp.id,
            match_id=mp.match_id,
            tournament_player_id=mp.tournament_player_id,
            player_id=mp.player_id,
            seat_position=mp.seat_position,
            score=mp.score,
            result=mp.result,
            tiebreaker_score=mp.tiebreaker_score,
            is_winner=mp.is_winner,
            start_rank=mp.start_rank,
            player={
                "id": player.id,
                "name": player.name,
                "team": player.team,
                "avatar": player.avatar,
            } if player else None
        ))

    return MatchResponse(
        id=match.id,
        tournament_id=match.tournament_id,
        room_id=match.room_id,
        round_number=match.round_number,
        table_number=match.table_number,
        status=match.status,
        start_time=match.start_time,
        end_time=match.end_time,
        locked_at=match.locked_at,
        submitted_by=match.submitted_by,
        submitted_at=match.submitted_at,
        referee_note=match.referee_note,
        match_players=player_responses,
        created_at=match.created_at,
        updated_at=match.updated_at
    )


@router.get("", response_model=List[MatchResponse])
def list_matches(
    tournament_id: Optional[int] = None,
    round_number: Optional[int] = None,
    status: Optional[str] = None,
    room_id: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    query = db.query(Match)
    if tournament_id:
        query = query.filter(Match.tournament_id == tournament_id)
    if round_number:
        query = query.filter(Match.round_number == round_number)
    if status:
        query = query.filter(Match.status == status)
    if room_id:
        query = query.filter(Match.room_id == room_id)

    matches = query.order_by(
        Match.round_number.asc(),
        Match.table_number.asc()
    ).offset(skip).limit(limit).all()

    return [_build_match_response(m, db) for m in matches]


@router.post("", response_model=MatchResponse)
def create_match(match: MatchCreate, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == match.tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    if match.room_id:
        room = db.query(Room).filter(Room.id == match.room_id).first()
        if not room:
            raise HTTPException(status_code=404, detail="房间不存在")
        if room.tournament_id != match.tournament_id:
            raise HTTPException(
                status_code=400,
                detail=f"房间ID={match.room_id} 不属于当前赛事(tournament_id={match.tournament_id})，"
                       f"该房间属于赛事(tournament_id={room.tournament_id})"
            )

    if not match.match_players:
        raise HTTPException(status_code=400, detail="对局选手列表不能为空")

    seen_seats = set()
    seen_tp_ids = set()
    seen_player_ids = set()
    for mp_data in match.match_players:
        if mp_data.seat_position in seen_seats:
            raise HTTPException(
                status_code=400,
                detail=f"座位号 seat_position={mp_data.seat_position} 重复，请检查"
            )
        seen_seats.add(mp_data.seat_position)

        if mp_data.tournament_player_id in seen_tp_ids:
            raise HTTPException(
                status_code=400,
                detail=f"报名记录 tournament_player_id={mp_data.tournament_player_id} 重复"
            )
        seen_tp_ids.add(mp_data.tournament_player_id)

        if mp_data.player_id in seen_player_ids:
            raise HTTPException(
                status_code=400,
                detail=f"选手 player_id={mp_data.player_id} 在本桌重复出现"
            )
        seen_player_ids.add(mp_data.player_id)

        reg = db.query(TournamentPlayer).filter(
            TournamentPlayer.id == mp_data.tournament_player_id
        ).first()
        if not reg:
            raise HTTPException(
                status_code=404,
                detail=f"报名记录 tournament_player_id={mp_data.tournament_player_id} 不存在"
            )
        if reg.tournament_id != match.tournament_id:
            raise HTTPException(
                status_code=400,
                detail=f"报名记录ID={mp_data.tournament_player_id} 不属于当前赛事"
                       f"(当前赛事={match.tournament_id}，该报名属于赛事={reg.tournament_id})"
            )
        if reg.player_id != mp_data.player_id:
            raise HTTPException(
                status_code=400,
                detail=f"选手ID不匹配：报名记录 tournament_player_id={mp_data.tournament_player_id} "
                       f"对应的选手是 player_id={reg.player_id}，但传入的是 player_id={mp_data.player_id}"
            )
        if reg.drop_round is not None and reg.drop_round <= match.round_number:
            raise HTTPException(
                status_code=400,
                detail=f"选手 player_id={mp_data.player_id} 已在第{reg.drop_round}轮退赛，"
                       f"无法参加第{match.round_number}轮"
            )

    db_match = Match(
        tournament_id=match.tournament_id,
        room_id=match.room_id,
        round_number=match.round_number,
        table_number=match.table_number,
        status=MatchStatus.PENDING.value
    )
    db.add(db_match)
    db.flush()

    for mp_data in match.match_players:
        mp = MatchPlayer(
            match_id=db_match.id,
            tournament_player_id=mp_data.tournament_player_id,
            player_id=mp_data.player_id,
            seat_position=mp_data.seat_position,
            start_rank=mp_data.start_rank
        )
        db.add(mp)

    db.commit()
    db.refresh(db_match)
    return _build_match_response(db_match, db)


@router.get("/{match_id}", response_model=MatchResponse)
def get_match(match_id: int, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="对局不存在")
    return _build_match_response(match, db)


@router.post("/{match_id}/lock", response_model=MatchResponse)
def lock_match_lineup(match_id: int, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="对局不存在")

    if match.status not in [MatchStatus.PENDING.value]:
        raise HTTPException(status_code=400, detail=f"当前状态为{match.status}，无法锁定阵容")

    match_players = db.query(MatchPlayer).filter(MatchPlayer.match_id == match_id).all()
    player_count = len(match_players)
    if player_count < 2:
        raise HTTPException(status_code=400, detail="至少需要2名选手才能开局")

    seen_seats = set()
    for mp in match_players:
        if mp.seat_position in seen_seats:
            raise HTTPException(
                status_code=400,
                detail=f"数据异常：座位号 seat_position={mp.seat_position} 重复，无法锁定"
            )
        seen_seats.add(mp.seat_position)

    for mp in match_players:
        reg = db.query(TournamentPlayer).filter(
            TournamentPlayer.id == mp.tournament_player_id
        ).first()
        if not reg:
            raise HTTPException(
                status_code=400,
                detail=f"数据异常：对局选手 tournament_player_id={mp.tournament_player_id} 对应报名记录不存在"
            )
        if reg.tournament_id != match.tournament_id:
            raise HTTPException(
                status_code=400,
                detail=f"数据异常：选手 player_id={mp.player_id} 的报名记录属于赛事 tournament_id={reg.tournament_id}，"
                       f"但本对局属于赛事 tournament_id={match.tournament_id}"
            )
        if reg.player_id != mp.player_id:
            raise HTTPException(
                status_code=400,
                detail=f"数据异常：报名记录 tournament_player_id={mp.tournament_player_id} "
                       f"对应的选手 player_id={reg.player_id} 与对局记录 player_id={mp.player_id} 不一致"
            )
        if reg.drop_round is not None and reg.drop_round <= match.round_number:
            raise HTTPException(
                status_code=400,
                detail=f"选手 player_id={mp.player_id} 已在第{reg.drop_round}轮退赛，无法锁定阵容"
            )

    match.status = MatchStatus.LOCKED.value
    match.locked_at = datetime.now()
    match.start_time = datetime.now()
    db.commit()
    db.refresh(match)

    return _build_match_response(match, db)


@router.post("/{match_id}/start", response_model=MatchResponse)
def start_match(match_id: int, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="对局不存在")

    if match.status == MatchStatus.PENDING.value:
        match.status = MatchStatus.LOCKED.value
        match.locked_at = datetime.now()

    if match.status not in [MatchStatus.LOCKED.value]:
        raise HTTPException(status_code=400, detail=f"当前状态为{match.status}，无法开始对局")

    match.status = MatchStatus.ONGOING.value
    if not match.start_time:
        match.start_time = datetime.now()
    db.commit()
    db.refresh(match)

    return _build_match_response(match, db)


@router.post("/{match_id}/submit-result", response_model=MatchResponse)
def submit_match_result(
    match_id: int,
    result: MatchResultSubmit,
    db: Session = Depends(get_db)
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="对局不存在")

    if match.status == MatchStatus.FINISHED.value:
        existing_data = serialize_match_scores(
            db.query(MatchPlayer).filter(MatchPlayer.match_id == match_id).all()
        )
        new_scores_data = [s.model_dump() for s in result.scores]
        new_hash = generate_submission_hash(match_id, new_scores_data)

        is_same_result = False
        if match.submission_hash and match.submission_hash == new_hash:
            is_same_result = True

        error_msg = ""
        if is_same_result:
            error_msg = (
                "⚠️ 检测到重复提交：成绩与已提交的完全一致，原成绩不会被覆盖。\n"
                "📋 该对局已提交的成绩已存在，如需修改请使用【裁判改判】接口：\n"
                "   POST /api/v1/referees/matches/{match_id}/override\n"
                "   改判会记录操作人、时间、原因和变更内容，全程留痕可审计。"
            )
        else:
            error_msg = (
                "🚫 该对局已提交过成绩，不能再次通过普通提交接口修改。\n"
                f"📌 当前对局状态：{match.status}，提交人：{match.submitted_by or '未知'}，提交时间：{match.submitted_at}\n"
                "📋 如需修改比分，请使用【裁判改判】接口：\n"
                "   POST /api/v1/referees/matches/{match_id}/override\n"
                "   改判需要提供裁判ID和修改原因，系统将自动记录完整的审计日志。"
            )

        raise HTTPException(status_code=409, detail=error_msg)

    if match.status == MatchStatus.OVERRIDDEN.value:
        raise HTTPException(
            status_code=409,
            detail=(
                "🚫 该对局已被裁判改判，不能再次通过普通提交接口修改。\n"
                "📋 如需再次修改，请继续使用【裁判改判】接口，所有改判操作都会被完整记录。\n"
                "   查看改判历史：GET /api/v1/referees/matches/{match_id}/change-logs"
            )
        )

    if match.status not in [MatchStatus.LOCKED.value, MatchStatus.ONGOING.value]:
        raise HTTPException(
            status_code=400,
            detail=f"当前状态为{match.status}，无法提交结果。允许的状态：locked、ongoing"
        )

    valid_results = [r.value for r in ResultType]
    for s in result.scores:
        if s.result not in valid_results:
            raise HTTPException(
                status_code=400,
                detail=f"无效的比赛结果: {s.result}，有效值为 {valid_results}"
            )
        if s.score < 0:
            raise HTTPException(
                status_code=400,
                detail=f"选手 match_player_id={s.match_player_id} 的分数不能为负数"
            )

    scores_data = [s.model_dump() for s in result.scores]
    submission_hash = generate_submission_hash(match_id, scores_data)

    existing_mp_ids = {mp.id for mp in db.query(MatchPlayer).filter(MatchPlayer.match_id == match_id).all()}
    submitted_ids = {s.match_player_id for s in result.scores}
    missing_ids = existing_mp_ids - submitted_ids
    extra_ids = submitted_ids - existing_mp_ids

    if missing_ids:
        raise HTTPException(status_code=400, detail=f"缺少选手成绩: {list(missing_ids)}")
    if extra_ids:
        raise HTTPException(status_code=400, detail=f"无效的对局选手ID: {list(extra_ids)}（不属于本场对局）")

    for s in result.scores:
        mp = db.query(MatchPlayer).filter(MatchPlayer.id == s.match_player_id).first()
        if mp and mp.match_id != match_id:
            raise HTTPException(
                status_code=400,
                detail=f"选手 match_player_id={s.match_player_id} 不属于本场对局 match_id={match_id}"
            )

    for s in result.scores:
        mp = db.query(MatchPlayer).filter(MatchPlayer.id == s.match_player_id).first()
        if mp:
            mp.score = s.score
            mp.result = s.result
            mp.tiebreaker_score = s.tiebreaker_score or 0.0
            mp.is_winner = (s.result == ResultType.WIN.value)

    match.status = MatchStatus.FINISHED.value
    match.submitted_by = result.submitted_by
    match.submitted_at = datetime.now()
    match.submission_hash = submission_hash
    if result.end_time:
        match.end_time = result.end_time
    else:
        match.end_time = datetime.now()
    if result.referee_note:
        match.referee_note = result.referee_note

    db.commit()
    db.refresh(match)

    return _build_match_response(match, db)


@router.delete("/{match_id}")
def delete_match(match_id: int, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="对局不存在")

    if match.status in [MatchStatus.FINISHED.value, MatchStatus.OVERRIDDEN.value]:
        raise HTTPException(status_code=400, detail="已完成或改判的对局无法删除，请联系裁判改判")

    db.query(MatchPlayer).filter(MatchPlayer.match_id == match_id).delete()
    db.delete(match)
    db.commit()
    return {"message": "对局已删除"}


@router.post("/{match_id}/cancel", response_model=MatchResponse)
def cancel_match(match_id: int, reason: Optional[str] = None, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="对局不存在")

    if match.status == MatchStatus.FINISHED.value:
        raise HTTPException(status_code=400, detail="已完成的对局无法取消，请联系裁判改判")

    match.status = MatchStatus.CANCELLED.value
    if reason:
        if match.referee_note:
            match.referee_note += f" | 取消原因: {reason}"
        else:
            match.referee_note = f"取消原因: {reason}"
    db.commit()
    db.refresh(match)

    return _build_match_response(match, db)


@router.get("/tournament/{tournament_id}/status-summary")
def get_tournament_match_status_summary(
    tournament_id: int,
    db: Session = Depends(get_db)
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    matches = db.query(Match).filter(Match.tournament_id == tournament_id).all()

    summary = {}
    max_round = 0
    for match in matches:
        rn = match.round_number
        max_round = max(max_round, rn)
        if rn not in summary:
            summary[rn] = {
                "round": rn,
                "total": 0,
                "pending": 0,
                "locked": 0,
                "ongoing": 0,
                "finished": 0,
                "cancelled": 0,
                "overridden": 0,
            }
        summary[rn]["total"] += 1
        status_key = match.status
        if status_key in summary[rn]:
            summary[rn][status_key] += 1

    rounds = []
    for rn in sorted(summary.keys()):
        rounds.append(summary[rn])

    total = len(matches)
    finished = sum(s["finished"] + s["overridden"] for s in rounds)

    return {
        "tournament_id": tournament_id,
        "tournament_name": tournament.name,
        "total_rounds": tournament.total_rounds,
        "rounds_with_matches": max_round,
        "total_matches": total,
        "completed_matches": finished,
        "pending_matches": total - finished - sum(s["cancelled"] for s in rounds),
        "round_summary": rounds,
        "completion_rate": round(finished / total * 100, 2) if total > 0 else 0,
    }
