from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime
import enum


class PlayerBase(BaseModel):
    name: str = Field(..., max_length=100)
    avatar: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=100)
    id_card: Optional[str] = Field(None, max_length=50)
    team: Optional[str] = Field(None, max_length=100)
    rating: Optional[int] = 1000
    status: Optional[str] = "active"
    note: Optional[str] = None


class PlayerCreate(PlayerBase):
    pass


class PlayerUpdate(BaseModel):
    name: Optional[str] = None
    avatar: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    id_card: Optional[str] = None
    team: Optional[str] = None
    rating: Optional[int] = None
    status: Optional[str] = None
    note: Optional[str] = None


class PlayerResponse(PlayerBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TournamentBase(BaseModel):
    name: str = Field(..., max_length=200)
    description: Optional[str] = None
    game_type: str = Field(..., max_length=100)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    location: Optional[str] = Field(None, max_length=200)
    max_players: Optional[int] = 100
    players_per_room: Optional[int] = 4
    total_rounds: Optional[int] = 5
    win_points: Optional[float] = 3.0
    draw_points: Optional[float] = 1.0
    lose_points: Optional[float] = 0.0
    status: Optional[str] = "draft"
    is_test: Optional[bool] = False
    organizer: Optional[str] = Field(None, max_length=100)
    rules: Optional[str] = None


class TournamentCreate(TournamentBase):
    pass


class TournamentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    game_type: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    location: Optional[str] = None
    max_players: Optional[int] = None
    players_per_room: Optional[int] = None
    total_rounds: Optional[int] = None
    win_points: Optional[float] = None
    draw_points: Optional[float] = None
    lose_points: Optional[float] = None
    status: Optional[str] = None
    is_test: Optional[bool] = None
    organizer: Optional[str] = None
    rules: Optional[str] = None


class TournamentResponse(TournamentBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GroupBase(BaseModel):
    tournament_id: int
    name: str = Field(..., max_length=100)
    description: Optional[str] = None


class GroupCreate(GroupBase):
    pass


class GroupResponse(GroupBase):
    id: int

    class Config:
        from_attributes = True


class GroupWithPlayersResponse(GroupResponse):
    players: List[Dict[str, Any]] = []


class RegistrationBase(BaseModel):
    tournament_id: int
    player_id: int
    group_id: Optional[int] = None
    seed: Optional[int] = None
    is_substitute: Optional[bool] = False


class RegistrationCreate(RegistrationBase):
    pass


class RegistrationUpdate(BaseModel):
    group_id: Optional[int] = None
    seed: Optional[int] = None
    seat_number: Optional[int] = None
    checked_in: Optional[bool] = None
    is_substitute: Optional[bool] = None
    final_rank: Optional[int] = None
    drop_round: Optional[int] = None


class RegistrationResponse(BaseModel):
    id: int
    tournament_id: int
    player_id: int
    group_id: Optional[int] = None
    seed: Optional[int] = None
    seat_number: Optional[int] = None
    registration_time: datetime
    checked_in: bool
    checkin_time: Optional[datetime] = None
    is_substitute: bool
    final_rank: Optional[int] = None
    drop_round: Optional[int] = None
    player: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class RoomBase(BaseModel):
    tournament_id: int
    group_id: Optional[int] = None
    name: str = Field(..., max_length=100)
    room_number: int
    table_number: Optional[str] = Field(None, max_length=20)
    capacity: Optional[int] = 4


class RoomCreate(RoomBase):
    pass


class RoomUpdate(BaseModel):
    name: Optional[str] = None
    table_number: Optional[str] = None
    capacity: Optional[int] = None


class RoomResponse(RoomBase):
    id: int

    class Config:
        from_attributes = True


class SeatAssignment(BaseModel):
    room_id: int
    seat_position: int
    tournament_player_id: int
    player_id: int
    player_name: str


class SeatAssignmentResponse(BaseModel):
    room: Dict[str, Any]
    assignments: List[SeatAssignment] = []


class MatchPlayerCreate(BaseModel):
    tournament_player_id: int
    player_id: int
    seat_position: int
    start_rank: Optional[int] = None


class MatchBase(BaseModel):
    tournament_id: int
    room_id: Optional[int] = None
    round_number: int
    table_number: Optional[int] = None


class MatchCreate(MatchBase):
    match_players: List[MatchPlayerCreate] = []


class MatchPlayerScore(BaseModel):
    match_player_id: int
    score: float
    result: str
    tiebreaker_score: Optional[float] = 0.0


class MatchResultSubmit(BaseModel):
    scores: List[MatchPlayerScore]
    submitted_by: str = Field(..., max_length=100)
    end_time: Optional[datetime] = None
    referee_note: Optional[str] = None


class MatchPlayerResponse(BaseModel):
    id: int
    match_id: int
    tournament_player_id: int
    player_id: int
    seat_position: int
    score: float
    result: Optional[str] = None
    tiebreaker_score: float
    is_winner: bool
    start_rank: Optional[int] = None
    player: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class MatchResponse(MatchBase):
    id: int
    status: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    locked_at: Optional[datetime] = None
    submitted_by: Optional[str] = None
    submitted_at: Optional[datetime] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    rejection_count: Optional[int] = 0
    referee_note: Optional[str] = None
    match_players: List[MatchPlayerResponse] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MatchConfirmRequest(BaseModel):
    confirmed_by: str = Field(..., max_length=100)

class MatchRejectRequest(BaseModel):
    rejected_by: str = Field(..., max_length=100)
    rejection_reason: str = Field(..., min_length=1, max_length=1000)


class FoulBase(BaseModel):
    tournament_id: int
    match_id: Optional[int] = None
    player_id: int
    type: str = Field(..., max_length=100)
    description: Optional[str] = None
    penalty_points: Optional[float] = 0.0
    penalty_rank: Optional[int] = 0
    reported_by: Optional[str] = Field(None, max_length=100)


class FoulCreate(FoulBase):
    pass


class FoulUpdate(BaseModel):
    description: Optional[str] = None
    penalty_points: Optional[float] = None
    penalty_rank: Optional[int] = None
    approved: Optional[bool] = None
    approved_by: Optional[str] = None


class FoulResponse(FoulBase):
    id: int
    approved: bool
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    reported_at: datetime

    class Config:
        from_attributes = True


class RefereeBase(BaseModel):
    name: str = Field(..., max_length=100)
    avatar: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=20)
    username: str = Field(..., max_length=50)
    role: Optional[str] = "assistant"
    is_active: Optional[bool] = True


class RefereeCreate(RefereeBase):
    password: str = Field(..., min_length=6, max_length=100)


class RefereeLogin(BaseModel):
    username: str
    password: str


class RefereeResponse(RefereeBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ScoreOverrideRequest(BaseModel):
    referee_id: int
    changes: List[MatchPlayerScore]
    reason: str


class ScoreChangeLogResponse(BaseModel):
    id: int
    match_id: int
    referee_id: Optional[int] = None
    change_type: str
    old_score_data: Optional[str] = None
    new_score_data: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class RankingItem(BaseModel):
    rank: int
    tournament_player_id: int
    player_id: int
    player_name: str
    team: Optional[str] = None
    played_rounds: int
    wins: int
    draws: int
    losses: int
    match_points: float
    opponent_match_win_percent: float
    game_win_percent: float
    opponent_game_win_percent: float
    total_tiebreaker: float
    foul_penalty: float


class RankingResponse(BaseModel):
    tournament_id: int
    round_number: int
    group_id: Optional[int] = None
    rankings: List[RankingItem] = []


class BigScreenRankingResponse(BaseModel):
    tournament_name: str
    round_number: int
    total_rounds: int
    tournament_status: str
    updated_at: datetime
    rankings: List[Dict[str, Any]] = []
    top_highlights: List[Dict[str, Any]] = []
    current_round_matches: int = 0
    completed_matches: int = 0


class NotificationBase(BaseModel):
    tournament_id: Optional[int] = None
    type: str = Field(..., max_length=30)
    title: str = Field(..., max_length=200)
    content: Optional[str] = None
    round_number: Optional[int] = None
    target_audience: Optional[str] = "all"


class NotificationCreate(NotificationBase):
    pass


class NotificationResponse(NotificationBase):
    id: int
    sent_at: datetime
    read_count: int

    class Config:
        from_attributes = True


class RoundStartNotification(BaseModel):
    tournament_id: int
    round_number: int
    custom_message: Optional[str] = None


class SubstitutionBase(BaseModel):
    tournament_id: int
    old_player_id: Optional[int] = None
    new_player_id: int
    reason: Optional[str] = Field(None, max_length=200)
    effective_round: int
    approved_by: Optional[str] = Field(None, max_length=100)


class SubstitutionCreate(SubstitutionBase):
    pass


class SubstitutionResponse(SubstitutionBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class PlayerHistoryResponse(BaseModel):
    player_id: int
    player_name: str
    total_matches: int
    total_wins: int
    total_losses: int
    total_draws: int
    win_rate: float
    tournaments: List[Dict[str, Any]] = []
    recent_matches: List[Dict[str, Any]] = []


class ExportFinalResultResponse(BaseModel):
    tournament_id: int
    tournament_name: str
    export_time: datetime
    data: Dict[str, Any]


class CleanTestDataResponse(BaseModel):
    tournament_id: Optional[int] = None
    deleted_counts: Dict[str, int]
    message: str
