import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops
from clipy import thumbs


class ThumbnailRendererTests(unittest.TestCase):
    def test_all_title_styles_and_positions_render_without_hyperframes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.png'
            Image.new('RGB', (360, 640), (50, 80, 110)).save(source)
            with patch('clipy.hyperframes.render_title', side_effect=AssertionError('unexpected HyperFrames render')):
                for style in thumbs.TITLE_STYLES:
                    with self.subTest(style=style):
                        out = root / f'{style}.png'
                        thumbs.add_title(source, out, 'Contoh judul', style, custom_text='Judul sendiri', number=2, sequence='episode', position='tr')
                        self.assertTrue(out.is_file())
                        with Image.open(source) as before, Image.open(out) as after:
                            self.assertEqual(after.size, before.size)
                            self.assertIsNotNone(ImageChops.difference(before.convert('RGB'), after.convert('RGB')).getbbox())


if __name__ == '__main__':
    unittest.main()
