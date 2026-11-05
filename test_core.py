"""Check local curve interpolation and validation."""
import copy
import unittest
from core import DEFAULT, duty_at, validate

class CurveTests(unittest.TestCase):
    def test_interpolation(self):
        self.assertEqual(duty_at([[30, 30], [70, 90], [85, 100]], 50), 60)

    def test_high_temperature_preview(self):
        self.assertEqual(duty_at(DEFAULT['points'], 95), 100)

    def test_decreasing_curve_rejected(self):
        config = copy.deepcopy(DEFAULT)
        config['points'][2][1] = 31
        with self.assertRaises(ValueError):
            validate(config)

if __name__ == '__main__':
    unittest.main()
