CREATE TABLE IF NOT EXISTS flights (
    id SERIAL PRIMARY KEY,
    flight_no VARCHAR(50),
    started_tracking TIMESTAMP,
    ended_tracking TIMESTAMP,
    flightpath_uuid UUID,
    from_airport VARCHAR(10),
    to_airport VARCHAR(10)
);
