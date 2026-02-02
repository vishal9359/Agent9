@echo off
REM Script to push Agent9 code to GitHub
REM Run this in your Agent9 directory

echo Initializing Git repository...
git init

echo Adding all files...
git add .

echo Creating commit...
git commit -m "Initial commit: C++ Flowchart Generator v2.0 - Fixed Unicode errors, empty flowcharts, and validation issues"

echo Adding remote repository...
git remote add origin https://github.com/vishal9359/Agent9.git

echo Setting branch to main...
git branch -M main

echo Pushing to GitHub...
git push -u origin main

echo.
echo Done! Check https://github.com/vishal9359/Agent9
pause
