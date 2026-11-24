-- A made-up Greek tutoring centre for trying database tools (Postgres MCP Pro) with Eidoloom Studio.
-- No real people: names are drawn at random from the lists below.

CREATE TABLE tutors (
    id serial PRIMARY KEY,
    name text NOT NULL,
    subject text NOT NULL
);

CREATE TABLE rooms (
    id serial PRIMARY KEY,
    name text NOT NULL UNIQUE,
    capacity int NOT NULL CHECK (capacity > 0)
);

CREATE TABLE students (
    id serial PRIMARY KEY,
    first_name text NOT NULL,
    last_name text NOT NULL,
    grade text NOT NULL,
    enrolled_on date NOT NULL
);

CREATE TABLE lessons (
    id serial PRIMARY KEY,
    tutor_id int NOT NULL REFERENCES tutors(id),
    room_id int NOT NULL REFERENCES rooms(id),
    starts_at timestamptz NOT NULL,
    minutes int NOT NULL CHECK (minutes BETWEEN 30 AND 180)
);

CREATE TABLE lesson_students (
    lesson_id int NOT NULL REFERENCES lessons(id),
    student_id int NOT NULL REFERENCES students(id),
    attended boolean NOT NULL DEFAULT true,
    PRIMARY KEY (lesson_id, student_id)
);

CREATE TABLE payments (
    id serial PRIMARY KEY,
    student_id int NOT NULL REFERENCES students(id),
    paid_on date NOT NULL,
    amount numeric(8, 2) NOT NULL CHECK (amount > 0)
);

INSERT INTO tutors (name, subject) VALUES
    ('Ελένη Παπαδοπούλου', 'Γλώσσα'), ('Νίκος Γεωργίου', 'Μαθηματικά'), ('Μαρία Κωνσταντίνου', 'Αγγλικά'),
    ('Γιώργος Δημητρίου', 'Φυσική'), ('Σοφία Αντωνίου', 'Γλώσσα'), ('Κώστας Ιωάννου', 'Χημεία');

INSERT INTO rooms (name, capacity) VALUES ('Αίθουσα 1', 4), ('Αίθουσα 2', 6), ('Αίθουσα 3', 2), ('Εργαστήριο', 8);

INSERT INTO students (first_name, last_name, grade, enrolled_on)
SELECT (ARRAY['Άννα','Δημήτρης','Κατερίνα','Παναγιώτης','Χριστίνα','Αλέξανδρος','Ιωάννα','Βασίλης','Δέσποινα','Θανάσης','Ευαγγελία','Στέλιος'])[1 + (i * 7) % 12],
       (ARRAY['Παππά','Νικολάου','Οικονόμου','Μαυρίδη','Αθανασίου','Βλάχου','Καραγιάννη','Σταθοπούλου','Λαμπράκη','Ζαχαρίου'])[1 + (i * 3) % 10],
       (ARRAY['Δ΄ Δημοτικού','Ε΄ Δημοτικού','ΣΤ΄ Δημοτικού','Α΄ Γυμνασίου','Β΄ Γυμνασίου','Γ΄ Γυμνασίου','Α΄ Λυκείου'])[1 + i % 7],
       date '2025-09-01' + (i * 5) % 300
FROM generate_series(1, 60) AS i;

INSERT INTO lessons (tutor_id, room_id, starts_at, minutes)
SELECT 1 + i % 6, 1 + (i * 7) % 4,
       timestamptz '2026-09-07 15:00+03' + ((i / 4) || ' days')::interval + ((i % 4) || ' hours')::interval,
       (ARRAY[45, 60, 90])[1 + i % 3]
FROM generate_series(0, 299) AS i;

INSERT INTO lesson_students (lesson_id, student_id, attended)
SELECT l.id, 1 + (l.id * 13 + k * 17) % 60, ((l.id + k) % 9) <> 0
FROM lessons l CROSS JOIN generate_series(0, 2) AS k
ON CONFLICT DO NOTHING;

INSERT INTO payments (student_id, paid_on, amount)
SELECT 1 + i % 60, date '2026-09-01' + (i % 70), (ARRAY[60, 80, 120, 150])[1 + i % 4]
FROM generate_series(0, 179) AS i;

CREATE INDEX lessons_starts_at ON lessons (starts_at);
