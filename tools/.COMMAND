git add .
git commit -m "update"
git push


sqlite3 database/inventory.db
DROP TABLE IF EXISTS table_name;
DELETE FROM admin_user WHERE username = "test";
SELECT * FROM book_title WHERE title = '成為更好的你';
SELECT * FROM book_title WHERE title LIKE '%成功%';