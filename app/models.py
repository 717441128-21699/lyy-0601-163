from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text, Enum, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base


class PlayerStatus(str, enum.Enum):
    ACTIVE = "active"
    INJURED = "injured"
    QUIT = "quit"
    BANNED = "banned"


class TournamentStatus(str, enum.Enum):
    DRAFT = "draft"
    REGISTRATION = "registration"
    READY = "ready"
    ONGOING = "ongoing"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class MatchStatus(str, enum.Enum):
    PENDING = "pending"
    LOCKED = "locked"
    ONGOING = "ongoing"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    OVERRIDDEN = "overridden"


class ResultType(str, enum.Enum):
    WIN = "win"
    LOSE = "lose"
    DRAW = "draw"
    BYE = "bye"


class RefereeRole(str, enum.Enum):
    MAIN = "main"
    ASSISTANT = "assistant"
    SCOREKEEPER = "scorekeeper"


class NotificationType(str, enum.Enum):
    ROUND_START = "round_start"
    MATCH_START = "match_start"
    RESULT_SUBMITTED = "result_submitted"
    REFEREE_CALLED = "referee_called"
    SUBSTITUTION = "substitution"
    GENERAL = "general"


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    avatar = Column(String(500), nullable=True)
    phone = Column(String(20), nullable=True)
    email = Column(String(100), nullable=True)
    id_card = Column(String(50), nullable=True, unique=True)
    team = Column(String(100), nullable=True)
    rating = Column(Integer, default=1000)
    status = Column(String(20), default=PlayerStatus.ACTIVE.value)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    tournament_registrations = relationship("TournamentPlayer", back_populates="player", cascade="all, delete-orphan")
    match_players = relationship("MatchPlayer", back_populates="player", cascade="all, delete-orphan")
    fouls = relationship("Foul", back_populates="player", cascade="all, delete-orphan")
    substitutions_as_old = relationship("Substitution", foreign_keys="Substitution.old_player_id", back_populates="old_player")
    substitutions_as_new = relationship("Substitution", foreign_keys="Substitution.new_player_id", back_populates="new_player")


class Tournament(Base):
    __tablename__ = "tournaments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    game_type = Column(String(100), nullable=False)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    location = Column(String(200), nullable=True)
    max_players = Column(Integer, default=100)
    players_per_room = Column(Integer, default=4)
    total_rounds = Column(Integer, default=5)
    win_points = Column(Float, default=3.0)
    draw_points = Column(Float, default=1.0)
    lose_points = Column(Float, default=0.0)
    status = Column(String(20), default=TournamentStatus.DRAFT.value)
    is_test = Column(Boolean, default=False)
    organizer = Column(String(100), nullable=True)
    rules = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    groups = relationship("Group", back_populates="tournament", cascade="all, delete-orphan")
    rooms = relationship("Room", back_populates="tournament", cascade="all, delete-orphan")
    registrations = relationship("TournamentPlayer", back_populates="tournament", cascade="all, delete-orphan")
    matches = relationship("Match", back_populates="tournament", cascade="all, delete-orphan")
    referees = relationship("TournamentReferee", back_populates="tournament", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="tournament", cascade="all, delete-orphan")
    substitutions = relationship("Substitution", back_populates="tournament", cascade="all, delete-orphan")


class TournamentPlayer(Base):
    __tablename__ = "tournament_players"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    seed = Column(Integer, nullable=True)
    seat_number = Column(Integer, nullable=True)
    registration_time = Column(DateTime, server_default=func.now())
    checked_in = Column(Boolean, default=False)
    checkin_time = Column(DateTime, nullable=True)
    is_substitute = Column(Boolean, default=False)
    final_rank = Column(Integer, nullable=True)
    drop_round = Column(Integer, nullable=True)

    tournament = relationship("Tournament", back_populates="registrations")
    player = relationship("Player", back_populates="tournament_registrations")
    group = relationship("Group", back_populates="players")
    match_players = relationship("MatchPlayer", back_populates="registration")

    __table_args__ = (UniqueConstraint("tournament_id", "player_id", name="_tournament_player_uc"),)


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)

    tournament = relationship("Tournament", back_populates="groups")
    players = relationship("TournamentPlayer", back_populates="group")
    rooms = relationship("Room", back_populates="group")


class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=False)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    name = Column(String(100), nullable=False)
    room_number = Column(Integer, nullable=False)
    table_number = Column(String(20), nullable=True)
    capacity = Column(Integer, default=4)

    tournament = relationship("Tournament", back_populates="rooms")
    group = relationship("Group", back_populates="rooms")
    matches = relationship("Match", back_populates="room")


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=True)
    round_number = Column(Integer, nullable=False)
    table_number = Column(Integer, nullable=True)
    status = Column(String(20), default=MatchStatus.PENDING.value)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    locked_at = Column(DateTime, nullable=True)
    submitted_by = Column(String(100), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    submission_hash = Column(String(64), nullable=True, unique=True)
    referee_note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    tournament = relationship("Tournament", back_populates="matches")
    room = relationship("Room", back_populates="matches")
    match_players = relationship("MatchPlayer", back_populates="match", cascade="all, delete-orphan")
    fouls = relationship("Foul", back_populates="match", cascade="all, delete-orphan")
    score_change_logs = relationship("ScoreChangeLog", back_populates="match", cascade="all, delete-orphan")


class MatchPlayer(Base):
    __tablename__ = "match_players"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    tournament_player_id = Column(Integer, ForeignKey("tournament_players.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    seat_position = Column(Integer, nullable=False)
    score = Column(Float, default=0.0)
    result = Column(String(10), nullable=True)
    tiebreaker_score = Column(Float, default=0.0)
    is_winner = Column(Boolean, default=False)
    start_rank = Column(Integer, nullable=True)

    match = relationship("Match", back_populates="match_players")
    registration = relationship("TournamentPlayer", back_populates="match_players")
    player = relationship("Player", back_populates="match_players")


class Foul(Base):
    __tablename__ = "fouls"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=False)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    type = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    penalty_points = Column(Float, default=0.0)
    penalty_rank = Column(Integer, default=0)
    reported_by = Column(String(100), nullable=True)
    reported_at = Column(DateTime, server_default=func.now())
    approved = Column(Boolean, default=True)
    approved_by = Column(String(100), nullable=True)
    approved_at = Column(DateTime, nullable=True)

    match = relationship("Match", back_populates="fouls")
    player = relationship("Player", back_populates="fouls")


class Referee(Base):
    __tablename__ = "referees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    avatar = Column(String(500), nullable=True)
    phone = Column(String(20), nullable=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default=RefereeRole.ASSISTANT.value)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

    tournament_assignments = relationship("TournamentReferee", back_populates="referee", cascade="all, delete-orphan")
    score_changes = relationship("ScoreChangeLog", back_populates="referee")


class TournamentReferee(Base):
    __tablename__ = "tournament_referees"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=False)
    referee_id = Column(Integer, ForeignKey("referees.id"), nullable=False)
    assigned_role = Column(String(20), nullable=True)
    assigned_at = Column(DateTime, server_default=func.now())

    tournament = relationship("Tournament", back_populates="referees")
    referee = relationship("Referee", back_populates="tournament_assignments")


class ScoreChangeLog(Base):
    __tablename__ = "score_change_logs"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    referee_id = Column(Integer, ForeignKey("referees.id"), nullable=True)
    change_type = Column(String(50), nullable=False)
    old_score_data = Column(Text, nullable=True)
    new_score_data = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    match = relationship("Match", back_populates="score_change_logs")
    referee = relationship("Referee", back_populates="score_changes")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=True)
    type = Column(String(30), nullable=False)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=True)
    round_number = Column(Integer, nullable=True)
    target_audience = Column(String(50), default="all")
    sent_at = Column(DateTime, server_default=func.now())
    read_count = Column(Integer, default=0)

    tournament = relationship("Tournament", back_populates="notifications")


class Substitution(Base):
    __tablename__ = "substitutions"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=False)
    old_player_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    new_player_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    reason = Column(String(200), nullable=True)
    effective_round = Column(Integer, nullable=False)
    approved_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    tournament = relationship("Tournament", back_populates="substitutions")
    old_player = relationship("Player", foreign_keys=[old_player_id], back_populates="substitutions_as_old")
    new_player = relationship("Player", foreign_keys=[new_player_id], back_populates="substitutions_as_new")
