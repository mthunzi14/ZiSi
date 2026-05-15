import express from 'express';
import { readTradesFile } from '../utils/fileReader.js';

const router = express.Router();

router.get('/', (req, res) => {
  try {
    const trades = readTradesFile();
    res.json({ trades, count: trades.length });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

router.get('/:id', (req, res) => {
  try {
    const trades = readTradesFile();
    const trade = trades.find(t => t.order_id === req.params.id);

    if (!trade) {
      return res.status(404).json({ error: 'Trade not found' });
    }

    res.json(trade);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

export default router;
