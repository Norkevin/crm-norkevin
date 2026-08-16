# -*- coding: utf-8 -*-
"""Regenera los iconos de la PWA con fondo BLANCO y la F azul.

Kevin: "el icono que sale en el celular que sea el blanco con la f azul".
El que salia en el telefono era apple-touch-icon.png / icon-512-maskable.png,
que tenian fondo azul solido. Los de 192/512 tenian el fondo transparente, que
en iOS se rellena de negro al agregar a la pantalla de inicio.

La fuente del glifo es static/icons/icon-512.png (F azul sobre transparente).
Correr con: python tools/build_icons.py
"""
import os

from PIL import Image

ICONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'static', 'icons')
SOURCE = os.path.join(ICONS, 'icon-512.png')
WHITE = (255, 255, 255, 255)


def on_white(glyph, size, scale):
    """Compone el glifo centrado sobre un cuadrado blanco.

    `scale` es que fraccion del lienzo ocupa el glifo. Para el icono maskable
    Android recorta hasta un circulo inscrito, asi que el contenido tiene que
    quedar dentro del 80% central -- por eso ahi se usa un scale mas chico.
    """
    canvas = Image.new('RGBA', (size, size), WHITE)
    box = max(1, int(size * scale))
    g = glyph.resize((box, box), Image.LANCZOS)
    off = (size - box) // 2
    canvas.alpha_composite(g, (off, off))
    return canvas.convert('RGB')


def main():
    glyph = Image.open(SOURCE).convert('RGBA')

    # El PNG original ya trae aire alrededor de la F; se recorta al contenido
    # real para poder controlar el margen nosotros.
    bbox = glyph.getbbox()
    if bbox:
        glyph = glyph.crop(bbox)
    w, h = glyph.size
    side = max(w, h)
    square = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    square.alpha_composite(glyph, ((side - w) // 2, (side - h) // 2))

    outputs = [
        ('icon-192.png', 192, 0.68),
        ('icon-512.png', 512, 0.68),
        ('apple-touch-icon.png', 180, 0.68),
        # Zona segura de maskable: el contenido no puede pasar del 80% central.
        ('icon-512-maskable.png', 512, 0.52),
    ]
    for name, size, scale in outputs:
        img = on_white(square, size, scale)
        img.save(os.path.join(ICONS, name), 'PNG', optimize=True)
        print(f'  {name:26} {size}x{size}  glifo al {int(scale * 100)}%')


if __name__ == '__main__':
    main()
