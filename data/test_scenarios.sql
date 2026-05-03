-- Test scenarios for Map Chat evaluation
CREATE TABLE IF NOT EXISTS test_scenarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    question    TEXT NOT NULL,
    expected_answer TEXT,
    category    TEXT,
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO test_scenarios (name, lat, lon, question, category, description) VALUES
('תל אביב - רחוב דיזנגוף',    32.0784, 34.7742, 'מה יש כאן?', 'urban',    'רחוב ראשי בתל אביב'),
('ירושלים - העיר העתיקה',     31.7767, 35.2345, 'מה יש כאן?', 'historic', 'מרכז היסטורי'),
('חיפה - הכרמל',              32.7940, 34.9896, 'מה יש כאן?', 'urban',    'שכונה על הכרמל'),
('באר שבע - מרכז העיר',       31.2518, 34.7913, 'מה יש כאן?', 'urban',    'בירת הנגב'),
('תל אביב - נווה צדק',        32.0598, 34.7650, 'מה יש כאן?', 'historic', 'שכונה היסטורית'),
('עפולה - מרכז',              32.6065, 35.2897, 'מה יש כאן?', 'suburban', 'עיר בעמק יזרעאל');