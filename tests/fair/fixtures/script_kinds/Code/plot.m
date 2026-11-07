cd('C:/Users/me/project');
t = readtable('data/scores.csv');
plot(t.id, t.score);
