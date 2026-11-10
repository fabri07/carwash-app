"""Personalización — FASE-4-CONTRATO §3.5 y §4 (PR 4.5)."""

from app.domain.branding import Font, Oklch, Palette
from app.schemas.common import CamelModel


class BrandColor(CamelModel):
    #: `oklch(56.0% 0.121 237.5)`: el valor del catálogo, para CSS.
    oklch: str
    #: El mismo color en sRGB (`#0a7db2`), para quien no habla OKLCH (el tema HSL del front).
    hex: str

    @classmethod
    def of(cls, color: Oklch) -> "BrandColor":
        return cls(oklch=color.css(), hex=color.hex())


class PaletteOut(CamelModel):
    code: str
    name: str
    background: BrandColor
    primary: BrandColor
    secondary: BrandColor
    on_primary: BrandColor

    @classmethod
    def of(cls, palette: Palette) -> "PaletteOut":
        return cls(
            code=palette.code,
            name=palette.name,
            background=BrandColor.of(palette.background),
            primary=BrandColor.of(palette.primary),
            secondary=BrandColor.of(palette.secondary),
            on_primary=BrandColor.of(palette.on_primary),
        )


class FontOut(CamelModel):
    code: str
    name: str
    #: Familia CSS; `null` = la del sistema.
    family: str | None
    description: str

    @classmethod
    def of(cls, font: Font) -> "FontOut":
        return cls(code=font.code, name=font.name, family=font.family, description=font.description)


class BrandingCatalog(CamelModel):
    """Catálogo fijo (en código): igual para todos los negocios."""

    palettes: list[PaletteOut]
    fonts: list[FontOut]
    default_palette: str
    default_font: str


class PaletteSuggestion(CamelModel):
    #: Código de la paleta sugerida.
    palette: str
    #: Color dominante del logo (`#rrggbb`); `null` si la imagen es toda transparente (entonces
    #: se sugiere la paleta por defecto).
    dominant_color: str | None


class LogoResponse(CamelModel):
    logo_key: str
    logo_url: str | None
    suggestion: PaletteSuggestion
