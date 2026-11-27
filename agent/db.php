<?php
// ЖЁСТКО задаём токены как константы PHP (без кавычек вокруг значений!)
define('RAG_API_TOKEN', '1337');
define('RAG_REVIEW_TOKEN', '322');

$host = 'localhost';
$dbname = 'agent';
$username = 'root';
$password = '';

try {
    $pdo = new PDO(
        "mysql:host=$host;dbname=$dbname;charset=utf8mb4",
        $username,
        $password,
        [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC
        ]
    );
} catch (PDOException $e) {
    die("Ошибка подключения: " . $e->getMessage());
}