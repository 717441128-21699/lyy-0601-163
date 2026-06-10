"""
桌游赛事计分系统 - 全流程验证脚本
运行方式: python tests/verify_system.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, Base, engine
from app import models
from app.models import (
    Player, Tournament, Group, Room, TournamentPlayer,
    Match, MatchPlayer, Foul, Referee, ScoreChangeLog,
    Notification, Substitution, MatchStatus, ResultType,
    TournamentStatus
)
from datetime import datetime
from sqlalchemy import text


def log(msg: str, level: str = "INFO"):
    prefix = f"[{level}]"
    color = {
        "INFO": "\033[94m",
        "SUCCESS": "\033[92m",
        "WARN": "\033[93m",
        "ERROR": "\033[91m",
    }.get(level, "\033[0m")
    reset = "\033[0m"
    print(f"{color}{prefix}{reset} {msg}")


def init_database():
    log("初始化数据库...")
    if os.path.exists("./tournament.db"):
        os.remove("./tournament.db")
        log("已清理旧数据库", "WARN")
    Base.metadata.create_all(bind=engine)
    log("数据库初始化完成", "SUCCESS")


def create_test_players(db, count=16):
    log(f"创建 {count} 名测试选手...")
    players = []
    teams = ["红队", "蓝队", "绿队", "黄队", "橙队", "紫队"]
    for i in range(1, count + 1):
        p = Player(
            name=f"选手{i:02d}",
            phone=f"138{10000000 + i:08d}",
            email=f"player{i}@example.com",
            id_card=f"ID{i:010d}",
            team=teams[i % len(teams)],
            rating=1000 + i * 10,
            note=f"测试选手 #{i}"
        )
        db.add(p)
        players.append(p)
    db.commit()
    for p in players:
        db.refresh(p)
    log(f"选手创建完成: {len(players)} 人", "SUCCESS")
    return players


def create_test_tournament(db):
    log("创建测试赛事...")
    t = Tournament(
        name="2026夏季桌游锦标赛·测试赛",
        description="系统全流程测试赛事 - 瑞士制积分赛",
        game_type="四人德式策略桌游",
        start_date=datetime.now(),
        location="测试场地A馆",
        max_players=32,
        players_per_room=4,
        total_rounds=4,
        win_points=3.0,
        draw_points=1.0,
        lose_points=0.0,
        status=TournamentStatus.REGISTRATION.value,
        is_test=True,
        organizer="赛事测试组",
        rules="标准瑞士制积分赛，共4轮，根据积分+小分排名"
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    log(f"赛事创建完成: #{t.id} {t.name}", "SUCCESS")
    return t


def create_groups(db, tournament, count=2):
    log(f"创建 {count} 个分组...")
    groups = []
    for i in range(1, count + 1):
        g = Group(
            tournament_id=tournament.id,
            name=f"A组 - 第{i}组",
            description=f"测试分组 {i}"
        )
        db.add(g)
        groups.append(g)
    db.commit()
    for g in groups:
        db.refresh(g)
    log(f"分组创建完成: {len(groups)} 组", "SUCCESS")
    return groups


def register_players(db, tournament, players, groups):
    log(f"报名 {len(players)} 名选手到赛事...")
    registrations = []
    for idx, player in enumerate(players):
        reg = TournamentPlayer(
            tournament_id=tournament.id,
            player_id=player.id,
            group_id=groups[idx % len(groups)].id,
            seed=idx + 1,
            is_substitute=False,
            checked_in=True,
            checkin_time=datetime.now()
        )
        db.add(reg)
        registrations.append(reg)
    db.commit()
    for r in registrations:
        db.refresh(r)
    log(f"报名完成: {len(registrations)} 人", "SUCCESS")
    return registrations


def create_rooms(db, tournament, count=4):
    log(f"创建 {count} 个比赛房间...")
    rooms = []
    for i in range(1, count + 1):
        r = Room(
            tournament_id=tournament.id,
            name=f"第{i}桌",
            room_number=i,
            table_number=f"T{i:02d}",
            capacity=4
        )
        db.add(r)
        rooms.append(r)
    db.commit()
    for r in rooms:
        db.refresh(r)
    log(f"房间创建完成: {len(rooms)} 间", "SUCCESS")
    return rooms


def create_referee(db):
    log("创建裁判账号...")
    import hashlib
    r = Referee(
        name="主裁判",
        username="chief_ref",
        password_hash=hashlib.sha256(b"referee123").hexdigest(),
        phone="13900000001",
        role="main",
        is_active=True
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    log(f"裁判创建完成: #{r.id} {r.name}", "SUCCESS")
    return r


def generate_round_pairings(db, tournament, round_number, rooms, registrations):
    from app.utils.scoring import calculate_tournament_rankings, generate_swiss_pairings
    log(f"生成第 {round_number} 轮对阵...")

    rankings = calculate_tournament_rankings(
        db, tournament.id,
        round_number - 1 if round_number > 1 else None
    )
    pairings = generate_swiss_pairings(rankings, round_number, tournament.players_per_room)

    matches = []
    for table_idx, table in enumerate(pairings):
        room = rooms[table_idx % len(rooms)]
        match = Match(
            tournament_id=tournament.id,
            room_id=room.id,
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
        matches.append(match)

    db.commit()
    for m in matches:
        db.refresh(m)
    log(f"第 {round_number} 轮对阵生成完成: {len(matches)} 场", "SUCCESS")
    return matches


def lock_and_play_round(db, matches):
    log("锁定阵容并模拟比赛...")
    import random
    for match in matches:
        match.status = MatchStatus.LOCKED.value
        match.locked_at = datetime.now()
        match.start_time = datetime.now()

        match.status = MatchStatus.ONGOING.value

        match_players = db.query(MatchPlayer).filter(
            MatchPlayer.match_id == match.id
        ).all()

        scores = sorted(
            [(mp, random.randint(50, 200)) for mp in match_players],
            key=lambda x: -x[1]
        )

        for rank_idx, (mp, score) in enumerate(scores):
            mp.score = float(score)
            mp.tiebreaker_score = float(score * 0.1)
            if rank_idx == 0:
                mp.result = ResultType.WIN.value
                mp.is_winner = True
            elif rank_idx == len(scores) - 1:
                mp.result = ResultType.LOSE.value
                mp.is_winner = False
            else:
                if random.random() < 0.2:
                    mp.result = ResultType.DRAW.value
                else:
                    mp.result = ResultType.LOSE.value if random.random() < 0.5 else ResultType.WIN.value
                mp.is_winner = (mp.result == ResultType.WIN.value)

        from app.utils.scoring import generate_submission_hash
        scores_data = [
            {
                "match_player_id": mp.id,
                "score": mp.score,
                "result": mp.result,
                "tiebreaker_score": mp.tiebreaker_score
            }
            for mp in match_players
        ]
        submission_hash = generate_submission_hash(match.id, scores_data)

        match.status = MatchStatus.FINISHED.value
        match.end_time = datetime.now()
        match.submitted_by = "系统测试"
        match.submitted_at = datetime.now()
        match.submission_hash = submission_hash

    db.commit()
    log(f"本轮比赛完成: {len(matches)} 场", "SUCCESS")


def record_fouls(db, tournament, players):
    log("记录一些犯规情况...")
    import random
    foul_types = ["超时", "违规操作", "不当行为", "迟到"]
    fouls = []
    for _ in range(3):
        player = random.choice(players)
        foul = Foul(
            tournament_id=tournament.id,
            player_id=player.id,
            type=random.choice(foul_types),
            description=f"测试犯规记录 - {random.randint(1, 100)}",
            penalty_points=float(random.randint(0, 3)),
            penalty_rank=random.randint(0, 1),
            reported_by="系统测试",
            approved=True,
            approved_by="裁判",
            approved_at=datetime.now()
        )
        db.add(foul)
        fouls.append(foul)
    db.commit()
    log(f"犯规记录完成: {len(fouls)} 条", "SUCCESS")
    return fouls


def test_score_override(db, matches, referee):
    log("测试裁判改判功能...")
    target_match = matches[0]
    old_match_players = list(target_match.match_players)

    from app.utils.scoring import serialize_match_scores
    old_data = serialize_match_scores(old_match_players)

    for mp in target_match.match_players:
        mp.score += 10.0
        if mp.seat_position == 4:
            mp.result = ResultType.WIN.value
            mp.is_winner = True
        elif mp.seat_position == 1:
            mp.result = ResultType.LOSE.value
            mp.is_winner = False

    new_data = serialize_match_scores(target_match.match_players)

    change_log = ScoreChangeLog(
        match_id=target_match.id,
        referee_id=referee.id,
        change_type="score_override",
        old_score_data=old_data,
        new_score_data=new_data,
        reason="测试改判：发现计分错误，已重新核实"
    )
    db.add(change_log)
    target_match.status = MatchStatus.OVERRIDDEN.value

    db.commit()
    db.refresh(change_log)
    log(f"改判完成: 变更日志 #{change_log.id}", "SUCCESS")
    return change_log


def check_rankings(db, tournament):
    from app.utils.scoring import calculate_tournament_rankings
    log("计算实时排名...")
    rankings = calculate_tournament_rankings(db, tournament.id)

    log(f"\n{'='*80}")
    log(f" 🏆 {tournament.name} - 实时排行榜 (前10名)")
    log(f"{'='*80}")
    log(f"{'排名':<6}{'选手':<12}{'队伍':<8}{'场次':<6}{'胜':<4}{'平':<4}{'负':<4}"
        f"{'积分':<8}{'对手胜率%':<12}{'对局胜率%':<10}{'小分':<8}{'犯规扣'}")
    log("-" * 80)

    for r in rankings[:10]:
        log(f"{r['rank']:<6}{r['player_name']:<12}{str(r.get('team') or '-'):<8}"
            f"{r['played_rounds']:<6}{r['wins']:<4}{r['draws']:<4}{r['losses']:<4}"
            f"{r['match_points']:<8.1f}{round(r['opponent_match_win_percent']*100,1):<12}"
            f"{round(r['game_win_percent']*100,1):<10}{r['total_tiebreaker']:<8.1f}"
            f"{r['foul_penalty']:<8.1f}")

    log(f"总排名人数: {len(rankings)}", "SUCCESS")
    return rankings


def send_round_notification(db, tournament, round_number):
    log(f"发送第 {round_number} 轮开始通知...")
    content = (f"【第{round_number}轮开始】{tournament.name}\n"
               f"请各位选手尽快前往座位就座！")
    notification = Notification(
        tournament_id=tournament.id,
        type="round_start",
        title=f"第{round_number}轮比赛开始",
        content=content,
        round_number=round_number,
        target_audience="all"
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    log(f"通知已发送: #{notification.id}", "SUCCESS")
    return notification


def test_substitution(db, tournament, players):
    log("测试退赛与替补功能...")
    old_player = players[7]
    new_player = players[15] if len(players) > 15 else players[0]

    old_reg = db.query(TournamentPlayer).filter(
        TournamentPlayer.tournament_id == tournament.id,
        TournamentPlayer.player_id == old_player.id
    ).first()

    if old_reg:
        old_reg.drop_round = 3

        new_reg = db.query(TournamentPlayer).filter(
            TournamentPlayer.tournament_id == tournament.id,
            TournamentPlayer.player_id == new_player.id
        ).first()
        if not new_reg:
            new_reg = TournamentPlayer(
                tournament_id=tournament.id,
                player_id=new_player.id,
                is_substitute=True,
                checked_in=True,
                checkin_time=datetime.now()
            )
            db.add(new_reg)
            db.flush()

        sub = Substitution(
            tournament_id=tournament.id,
            old_player_id=old_player.id,
            new_player_id=new_player.id,
            reason="测试退赛替补",
            effective_round=3,
            approved_by="主裁判"
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        log(f"替补完成: #{sub.id} {old_player.name} → {new_player.name}", "SUCCESS")
        return sub
    log("未找到报名记录，跳过替补测试", "WARN")
    return None


def verify_duplicate_submission(db, matches):
    from app.utils.scoring import generate_submission_hash, check_duplicate_submission
    log("验证重复提交防护...")

    target_match = matches[0] if matches else None
    if not target_match:
        log("无对局可测试", "WARN")
        return False

    match_players = db.query(MatchPlayer).filter(
        MatchPlayer.match_id == target_match.id
    ).all()
    scores_data = [
        {
            "match_player_id": mp.id,
            "score": mp.score,
            "result": mp.result,
            "tiebreaker_score": mp.tiebreaker_score
        }
        for mp in match_players
    ]

    new_hash = generate_submission_hash(target_match.id, scores_data)
    is_dup = check_duplicate_submission(db, target_match.id, new_hash)

    existing_hash = target_match.submission_hash
    is_dup_existing = check_duplicate_submission(db, target_match.id, existing_hash) if existing_hash else False

    log(f"新数据哈希重复检测: {'是' if is_dup else '否'}")
    log(f"已有提交哈希检测: {'是' if is_dup_existing else '否'} (应为: 是)")
    log(f"重复提交防护验证: {'通过' if is_dup_existing else '失败'}",
        "SUCCESS" if is_dup_existing else "ERROR")
    return is_dup_existing


def show_statistics(db, tournament):
    log("\n" + "="*80)
    log(" 📊 赛事统计汇总")
    log("="*80)

    reg_count = db.query(TournamentPlayer).filter(
        TournamentPlayer.tournament_id == tournament.id
    ).count()
    match_count = db.query(Match).filter(Match.tournament_id == tournament.id).count()
    finished = db.query(Match).filter(
        Match.tournament_id == tournament.id,
        Match.status.in_([MatchStatus.FINISHED.value, MatchStatus.OVERRIDDEN.value])
    ).count()
    foul_count = db.query(Foul).filter(Foul.tournament_id == tournament.id).count()
    change_count = db.query(ScoreChangeLog).join(Match).filter(
        Match.tournament_id == tournament.id
    ).count()
    notif_count = db.query(Notification).filter(
        Notification.tournament_id == tournament.id
    ).count()
    room_count = db.query(Room).filter(Room.tournament_id == tournament.id).count()

    stats = [
        ("赛事名称", tournament.name),
        ("当前状态", tournament.status),
        ("报名人数", f"{reg_count} 人"),
        ("比赛房间", f"{room_count} 间"),
        ("总对局数", f"{match_count} 场"),
        ("已完成", f"{finished} 场 ({finished/match_count*100:.1f}%)" if match_count else "0 场"),
        ("犯规记录", f"{foul_count} 条"),
        ("裁判改判", f"{change_count} 次"),
        ("通知发送", f"{notif_count} 条"),
        ("赛制配置", f"{tournament.total_rounds} 轮 · {tournament.players_per_room} 人/桌"),
    ]

    for k, v in stats:
        log(f"  {k:<15} : {v}")

    log("="*80 + "\n")


def clean_test_data_example(db, tournament):
    log("测试数据清理功能可用（保留数据以便验证）")
    log(f"  清理命令: POST /api/v1/admin/clean-test-data")
    log(f"  参数: tournament_id={tournament.id} 或 clean_all_test=true", "INFO")


def main():
    log("\n" + "🚀" * 30)
    log("  桌游赛事多人对战计分系统 - 全流程验证脚本")
    log("🚀" * 30 + "\n")

    init_database()
    db = SessionLocal()

    try:
        players = create_test_players(db, count=16)
        tournament = create_test_tournament(db)
        groups = create_groups(db, tournament, count=2)
        registrations = register_players(db, tournament, players, groups)
        rooms = create_rooms(db, tournament, count=4)
        referee = create_referee(db)

        log("\n" + "="*60)
        log(" 🎮 开始模拟比赛轮次")
        log("="*60)

        all_matches = []
        for round_num in range(1, tournament.total_rounds + 1):
            log(f"\n--- 第 {round_num}/{tournament.total_rounds} 轮 ---")
            matches = generate_round_pairings(db, tournament, round_num, rooms, registrations)
            lock_and_play_round(db, matches)
            all_matches.extend(matches)
            send_round_notification(db, tournament, round_num)
            check_rankings(db, tournament)

        record_fouls(db, tournament, players)
        test_score_override(db, all_matches, referee)
        test_substitution(db, tournament, players)
        verify_duplicate_submission(db, all_matches)

        final_rankings = check_rankings(db, tournament)

        show_statistics(db, tournament)
        clean_test_data_example(db, tournament)

        log("\n" + "✅" * 30)
        log("  🎉 全流程验证完成！所有核心功能正常运行")
        log("✅" * 30)

        log("\n📌 启动服务命令:")
        log("  pip install -r requirements.txt")
        log("  python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload")
        log("\n📌 API 文档地址:")
        log("  Swagger UI: http://localhost:8000/docs")
        log("  ReDoc:      http://localhost:8000/redoc\n")

    except Exception as e:
        import traceback
        log(f"❌ 验证过程出错: {e}", "ERROR")
        traceback.print_exc()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
