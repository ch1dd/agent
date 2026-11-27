<?php
// Скопируйте файл в db.php и задайте параметры своего сервера MySQL.
$host = '127.0.0.1';
$dbname = 'your_database';
$username = 'your_user';
$password = '';

$pdo = new PDO(
    "mysql:host=$host;dbname=$dbname;charset=utf8mb4",
    $username,
    $password,
    [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC
    ]
);
