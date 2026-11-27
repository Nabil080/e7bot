"""CoordMapper: converts between the 1280x720 game space, the client area and the screen."""

from __future__ import annotations

from core.window import ScreenRect

GAME_WIDTH = 1280
GAME_HEIGHT = 720


class CoordMapper:
    """Maps game coordinates to true screen pixels for one client rect.

    Build a new one whenever the client rect changes: it holds no live window state.
    """

    def __init__(self, client: ScreenRect, game_width: int = GAME_WIDTH, game_height: int = GAME_HEIGHT) -> None:
        """Store the client rect. Raises ValueError if it has no area (minimized window)."""
        if client.width <= 0 or client.height <= 0:
            raise ValueError(f"client rect has no area: {client}")
        self.client = client
        self.game_width = game_width
        self.game_height = game_height
        self.scale_x = client.width / game_width
        self.scale_y = client.height / game_height

    def in_game_bounds(self, x: float, y: float) -> bool:
        """Return whether a point is inside the game space."""
        return 0 <= x < self.game_width and 0 <= y < self.game_height

    def clamp(self, x: float, y: float) -> tuple[float, float]:
        """Return the nearest point inside the game space."""
        return (
            min(max(x, 0), self.game_width - 1),
            min(max(y, 0), self.game_height - 1),
        )

    def game_to_client(self, x: float, y: float) -> tuple[int, int]:
        """Convert a game point to client-area pixels."""
        return round(x * self.scale_x), round(y * self.scale_y)

    def game_to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Convert a game point to screen pixels. This is what mouse input needs."""
        client_x, client_y = self.game_to_client(x, y)
        return self.client.left + client_x, self.client.top + client_y

    def client_to_game(self, x: float, y: float) -> tuple[float, float]:
        """Convert client-area pixels to a game point. This is what a captured frame gives."""
        return x / self.scale_x, y / self.scale_y

    def screen_to_game(self, x: float, y: float) -> tuple[float, float]:
        """Convert screen pixels to a game point."""
        return self.client_to_game(x - self.client.left, y - self.client.top)
