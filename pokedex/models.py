from django.db import models

class Pokemon(models.Model):
    number = models.IntegerField(unique=True)
    name = models.CharField(max_length=100, unique=True)
    types = models.CharField(max_length=255)
    abilities = models.CharField(max_length=255)
    sprite = models.URLField()

    def __str__(self):
        return self.name
