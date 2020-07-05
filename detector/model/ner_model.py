import numpy as np
import os
import random
import tensorflow as tf
from sklearn.metrics import confusion_matrix
from sklearn.metrics import classification_report

from .rpn import detect_conflict
from .data_utils import minibatches, pad_sequences, get_chunks, pad_elmo_embedding, pad_bert_embedding
from .general_utils import Progbar
from .base_model import BaseModel

from tensorflow.contrib.rnn import BasicLSTMCell

random.seed(5)

class NERModel(BaseModel):
    """Specialized class of Model for NER"""

    def __init__(self, config):
        super(NERModel, self).__init__(config)
        self.idx_to_tag = {idx: tag for tag, idx in
                           self.config.vocab_tags.items()}

    def add_placeholders(self):
        """Define placeholders = entries to computational graph"""

        # shape = (batch size, max length of sentence in batch)
        self.word_ids = tf.placeholder(tf.int32, shape=[None, None],
                        name="word_ids")

        # shape = (batch size, max length of sentence in batch)
        self.postag_ids = tf.placeholder(tf.int32, shape=[None, None],
                        name="postag_ids")

        # shape = (batch size, 3, max_len, 1024)
        # self.elmo_embedding = tf.placeholder(tf.float32, shape=[None, 3, None, 1024],
        #                 name="elmo_embedding")
        #shape = (batch_sisze, max_len, 1024)
        self.bert_embedding = tf.placeholder(tf.float32, shape = [None, 3 ,None, 1024], name ="bert_embedding")
        # shape = (batch size)
        self.sequence_lengths = tf.placeholder(tf.int32, shape=[None],
                        name="sequence_lengths")

        # shape = (batch size, max length of sentence, max length of word)
        self.char_ids = tf.placeholder(tf.int32, shape=[None, None, None],
                       name="char_ids")

        # shape = (batch_size, max_length of sentence)
        self.word_lengths = tf.placeholder(tf.int32, shape=[None, None],
                       name="word_lengths")

        # shape = (batch size, max length of sentence in batch)
        self.labels = tf.placeholder(tf.int32, shape=[None, None],
                        name="labels")

        # shape = (batch size, max length of sentence in batch * anchor_type, 2)
        self.anchors = tf.placeholder(tf.int32, shape=[None, None, 2],
                        name="anchors")

        # shape = (batch size, max length of sentence in batch * anchor_type)
        self.anchor_labels = tf.placeholder(tf.int32, shape=[None, None],
                        name="anchor_labels")

        # hyper parameters
        self.dropout = tf.placeholder(dtype=tf.float32, shape=[],
                        name="dropout")
        self.lr = tf.placeholder(dtype=tf.float32, shape=[],
                        name="lr")
        self.sentence_len = tf.placeholder(dtype = tf.float32, shape = [None], name = "sentence_len")


    def get_feed_dict(self, words, bert_embedding= None, postags=None, anchors=None, anchor_labels=None,
            lr=None, dropout=None):
        """Given some data, pad it and build a feed dictionary

        Args:
            words: list of sentences. A sentence is a list of ids of a list of
                words. A word is a list of ids
            labels: list of ids
            lr: (float) learning rate
            dropout: (float) keep prob

        Returns:
            dict {placeholder: value}

        """

        # perform padding of the given data
        if self.config.use_chars:
            char_ids, word_ids = zip(*words)
            word_ids, sequence_lengths = pad_sequences(word_ids, 0)
            char_ids, word_lengths = pad_sequences(char_ids, pad_tok=0,
                nlevels=2)
        else:
            word_ids, sequence_lengths = pad_sequences(words, 0)

        # build feed dictionary
        feed = {
            self.word_ids: word_ids,
            self.sequence_lengths: sequence_lengths
        }

        if self.config.use_chars:
            feed[self.char_ids] = char_ids
            feed[self.word_lengths] = word_lengths

        if postags is not None:
            postags, _ = pad_sequences(postags, 0)
            feed[self.postag_ids] = postags

        # get elmo embedding
        # list of numpy array: batch_size of (3: layer_num, sentence_len, 1024)
        # if sentences is not None:
        #     padded_emb, _ = pad_elmo_embedding(sentences)
        #     feed[self.elmo_embedding] = padded_emb
        if bert_embedding is not None:
            # print("Run Here",bert_embedding[0])
            padded_bert, _ = pad_bert_embedding(bert_embedding)
            feed[self.bert_embedding] = padded_bert

        if anchors is not None:
            anchors, _ = pad_sequences(anchors, [-1, -1])
            feed[self.anchors] = anchors

        if anchor_labels is not None:
            anchor_labels, _ = pad_sequences(anchor_labels, -1)
            feed[self.anchor_labels] = anchor_labels

        if lr is not None:
            feed[self.lr] = lr

        if dropout is not None:
            feed[self.dropout] = dropout
			
        feed[self.sentence_len] = list(map(lambda x : x.shape[1], bert_embedding))

        return feed, sequence_lengths

    def add_word_embeddings_op(self):
        """Defines self.word_embeddings

        If self.config.embeddings is not None and is a np array initialized
        with pre-trained word vectors, the word embeddings is just a look-up
        and we don't train the vectors. Otherwise, a random matrix with
        the correct shape is initialized.
        """
        with tf.variable_scope("words"): # words
            if self.config.embeddings is None:
                self.logger.info("WARNING: randomly initializing word vectors")
                _word_embeddings = tf.get_variable(
                        name="_word_embeddings",
                        dtype=tf.float32,
                        shape=[self.config.nwords, self.config.dim_word])
            else:
                _word_embeddings = tf.Variable(
                        self.config.embeddings,
                        name="_word_embeddings",
                        dtype=tf.float32,
                        trainable=self.config.train_embeddings)

            self.word_embeddings = tf.nn.embedding_lookup(_word_embeddings,
                    self.word_ids, name="word_embeddings")

        with tf.variable_scope("postags"): # postags
            # random initialize pos tag embeddings
            _postags_embeddings = tf.get_variable(
                    name="_postags_embeddings",
                    dtype=tf.float32,
                    shape=[self.config.ntags, self.config.dim_postags])
            # lookup postag embeddings
            self.pos_embeddings = tf.nn.embedding_lookup(_postags_embeddings,
                    self.postag_ids, name="postags_embeddings")
            # concate word embedding with pos tag embedding
            self.word_embeddings = tf.concat([self.word_embeddings, \
                    self.pos_embeddings], axis=-1)

        with tf.variable_scope("chars"): # chars
            if self.config.use_chars:
                # get char embeddings matrix
                _char_embeddings = tf.get_variable(
                        name="_char_embeddings",
                        dtype=tf.float32,
                        shape=[self.config.nchars, self.config.dim_char])
                char_embeddings = tf.nn.embedding_lookup(_char_embeddings,
                        self.char_ids, name="char_embeddings")

                # put the time dimension on axis=1
                s = tf.shape(char_embeddings)
                char_embeddings = tf.reshape(char_embeddings,
                        shape=[s[0]*s[1], s[-2], self.config.dim_char])
                word_lengths = tf.reshape(self.word_lengths, shape=[s[0]*s[1]])

                # bi lstm on chars
                cell_fw = tf.contrib.rnn.LSTMCell(self.config.hidden_size_char,
                        state_is_tuple=True)
                cell_bw = tf.contrib.rnn.LSTMCell(self.config.hidden_size_char,
                        state_is_tuple=True)
                _output = tf.nn.bidirectional_dynamic_rnn(
                        cell_fw, cell_bw, char_embeddings,
                        sequence_length=word_lengths, dtype=tf.float32)

                # read and concat output
                _, ((_, output_fw), (_, output_bw)) = _output
                output = tf.concat([output_fw, output_bw], axis=-1)

                # shape = (batch size, max sentence length, char hidden size)
                output = tf.reshape(output,
                        shape=[s[0], s[1], 2*self.config.hidden_size_char])
                word_embeddings_cat = tf.concat([self.word_embeddings, output], axis=-1)

        self.word_embeddings_cat =  tf.nn.dropout(word_embeddings_cat, self.dropout)

    def add_lstm_1_op(self):
        """Defines self.logits

        For each word in each sentence of the batch, it corresponds to a vector
        of scores, of dimension equal to the number of tags.
        """
        with tf.variable_scope("lstm_1"): # bi-lstm
            cell_fw = tf.contrib.rnn.LSTMCell(self.config.hidden_size_lstm_1)
            cell_bw = tf.contrib.rnn.LSTMCell(self.config.hidden_size_lstm_1)
            (output_fw, output_bw), _ = tf.nn.bidirectional_dynamic_rnn(
                    cell_fw, cell_bw, self.word_embeddings_cat,
                    sequence_length=self.sequence_lengths, dtype=tf.float32)
            output = tf.concat([output_fw, output_bw], axis=-1)
            # lstm_output shape: [batch_size, time_step, hidden_dim * 2]
            self.lstm_1_output = tf.nn.dropout(output, self.dropout)

    def add_elmo_op(self):
        with tf.variable_scope("elmo"):
            # elmo layer weights
            elmo_layer_weights = tf.get_variable("elmo_layer_weights", dtype=tf.float32,
                    shape=[3], initializer = tf.contrib.layers.xavier_initializer())
            norm_weights = tf.nn.softmax(elmo_layer_weights)

            batch_num = tf.shape(self.elmo_embedding)[0]
            nsteps = tf.shape(self.elmo_embedding)[2]
            norm_weights = tf.expand_dims(tf.expand_dims(tf.expand_dims(norm_weights, 0), 2), 3)
            norm_weights = tf.tile(norm_weights, [batch_num, 1, nsteps, 1024])

            # self.elmo_embedding shape: [batch_size, 3, time_step, 1024]
            # elmo_embedding shape: [batch_size, time_step, 1024]
            weighted_elmo = tf.multiply(self.elmo_embedding, norm_weights)
            elmo_embedding = tf.reduce_sum(self.config.elmo_scale * weighted_elmo, axis=1)
            # concat elmo embedding and lstm_1_output
            # shape: [batch_size, time_step, 1024 + hidden_dim * 2]
            self.concat_hidden = tf.concat([elmo_embedding, self.lstm_1_output], axis=-1)
            # self.concat_hidden = tf.concat([self.concat_hidden, self.bert_embedding], axis = -1)
    def add_bert_op(self):
        with tf.variable_scope("bert"):
            bert_layer_weights = tf.get_variable("bert_layer_weights", dtype=tf.float32,
            shape=[3], initializer = tf.contrib.layers.xavier_initializer())
            norm_weights = tf.nn.softmax(bert_layer_weights)

            batch_num = tf.shape(self.bert_embedding)[0]
            nsteps = tf.shape(self.bert_embedding)[2]
            norm_weights = tf.expand_dims(tf.expand_dims(tf.expand_dims(norm_weights, 0), 2), 3)
            norm_weights = tf.tile(norm_weights, [batch_num, 1, nsteps, 1024])

            # self.elmo_embedding shape: [batch_size, 3, time_step, 1024]
            # elmo_embedding shape: [batch_size, time_step, 1024]
            weighted_bert = tf.multiply(self.bert_embedding, norm_weights)
            bert_embedding = tf.reduce_sum(self.config.elmo_scale * weighted_bert, axis=1)
            # concat elmo embedding and lstm_1_output
            # shape: [batch_size, time_step, 1024 + hidden_dim * 2]
            self.concat_hidden = tf.concat([bert_embedding, self.lstm_1_output], axis=-1)

    def add_lstm_2_op(self):
        """Defines self.logits

        For each word in each sentence of the batch, it corresponds to a vector
        of scores, of dimension equal to the number of tags.
        """
        with tf.variable_scope("lstm_2"): # bi-lstm
            cell_fw = tf.contrib.rnn.LSTMCell(self.config.hidden_size_lstm_2)
            cell_bw = tf.contrib.rnn.LSTMCell(self.config.hidden_size_lstm_2)
            (output_fw, output_bw), (last_hidden_fw, last_hidden_bw) = tf.nn.bidirectional_dynamic_rnn(
                    cell_fw, cell_bw, self.concat_hidden,
                    sequence_length=self.sequence_lengths, dtype=tf.float32)
            output = tf.concat([output_fw, output_bw], axis=-1)
            # lstm_output shape: [batch_size, time_step, hidden_dim * 2]
            self.lstm_output = tf.nn.dropout(output, self.dropout)
            self.sequence_last_hidden = tf.concat([last_hidden_fw[0], last_hidden_bw[0]], axis = -1)

            self.nsteps = tf.shape(self.lstm_output)[1]
            self.reshape_lstm = tf.reshape(self.lstm_output,
                    [-1, 2*self.config.hidden_size_lstm_2])
   

    def add_rpn_op(self):
        """
        Get rpn probablities
        """
        with tf.variable_scope("rpn"):

            W_rpn = tf.get_variable("W_rpn", dtype=tf.float32,
                    shape=[2*self.config.hidden_size_lstm_2,
                        self.config.anchor_types * 2],
                    initializer = tf.contrib.layers.xavier_initializer())
            b_rpn = tf.get_variable("b_rpn", dtype=tf.float32,
                    shape=[self.config.anchor_types * 2],
                    initializer= tf.contrib.layers.xavier_initializer())

            # score shape: [batch_size * nstpes, anchor_types * 2]
            rpn_score = tf.matmul(self.reshape_lstm, W_rpn) + b_rpn
            # reshape score : [batch_size * nsteps, anchor_types, 2]
            reshape_rpn_score = tf.reshape(rpn_score, [-1, self.config.anchor_types, 2])
            # prob shape : [batch_size * nsteps, anchor_types, 2]
            self.rpn_prob = tf.nn.softmax(reshape_rpn_score)
            # reshape prob :[batch_size, nstpes * anchor_types, 2]
            self.reshape_rpn_prob = tf.reshape(self.rpn_prob, [-1, self.nsteps * self.config.anchor_types, 2])

            # anchor_labels shape: [batch_size, nsteps * anchor_types]
            # gather positive labels
            pos_idx = tf.where(tf.equal(self.anchor_labels, 1))
            #pos_sample_num = tf.shape(pos_idx)[0]
            # gather negative labels
            neg_idx = tf.where(tf.equal(self.anchor_labels, 0))
            # subsample neg samples
            #sampled_neg_num = self.config.batch_sample - pos_sample_num
            #sampled_neg_idx = tf.random_shuffle(neg_idx)[:sampled_neg_num]
            # total sampled idx
            total_idx = tf.concat([pos_idx, neg_idx], 0)
            valid_rpn_label = tf.gather_nd(self.anchor_labels, total_idx)
            # onehot shape: [total_idx, 2]
			
			
            #self.onehot_rpn_label = tf.cast(valid_rpn_label, dtype = tf.float32)
            self.onehot_rpn_label = tf.one_hot(valid_rpn_label, depth = 2)
            # valid rpn prob: [total_idx, 2]
            self.valid_rpn_prob = tf.gather_nd(self.reshape_rpn_prob, total_idx)
			
            #self.valid_rpn_prob = self.valid_rpn_prob[:,1]

            # rpn classification loss
            #self.rpn_loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
            #			logits=self.valid_rpn_prob, labels=self.onehot_rpn_label))
			
            #result = self.onehot_rpn_label * tf.log(self.valid_rpn_prob)
            #result = - tf.reduce_sum(result, axis = 1)
            #self.rpn_loss = tf.reduce_mean(softmax_cross_entropy(logits = self.valid_rpn_prob, onehot_label = self.onehot_rpn_label)
            alpha = 0.35
            gamma = 2
            onehot_alpha = tf.concat([tf.fill([tf.shape(self.onehot_rpn_label)[0], 1], alpha), tf.fill([tf.shape(self.onehot_rpn_label)[0], 1],1 - alpha)], axis = 1)
            result = self.onehot_rpn_label * onehot_alpha * tf.pow(self.onehot_rpn_label - self.valid_rpn_prob,gamma) * tf.log(self.valid_rpn_prob)
            result = - tf.reduce_sum(result, axis = 1)
            self.rpn_loss = tf.reduce_mean(result)
			
			
			
    def softmax_focal_loss(gamma, alpha, logits, onehot_label):
        onehot_alpha = tf.concat([tf.fill([logits.shape[0], 1], alpha), tf.fill([logits.shape[0], 1],1 - alpha)], axis = 1)
        result = onehot_label * onehot_alpha * tf.pow(onehot_label - logits,gamma) * tf.log(logits)
        result = - tf.reduce_sum(loss, axis = 1)
        return result
    def softmax_cross_entropy(logits, onehot_label):
        result = onehot_label * tf.log(logits)
        result = - tf.reduce_sum(result, axis = 1)
        return result
    def add_loss_op(self):
        """Defines the loss"""
        # add rpn loss
        self.loss = self.rpn_loss

        # for tensorboard
        tf.summary.scalar("loss", self.loss)


    def build(self):
        # NER specific functions
        self.add_placeholders()
        self.add_word_embeddings_op()
        self.add_lstm_1_op()
        # self.add_elmo_op()
        self.add_bert_op()
        self.add_lstm_2_op()
        self.add_rpn_op()
        self.add_loss_op()

        # Generic functions that add training op and initialize session
        self.add_train_op(self.config.lr_method, self.lr, self.loss,
                self.config.clip)
        self.initialize_session() # now self.sess is defined and vars are init

    def predict_rpn(self, words, bert_embedding, postags):
        """
        Args:
            sentences: list of sentences

        Returns:
            labels_pred: list of labels for each sentence
            sequence_length

        """
        # fd, sequence_lengths = self.get_feed_dict(words,bert_embedding, postags, sentences, dropout=1.0)
        fd, sequence_lengths = self.get_feed_dict(words,bert_embedding, postags, dropout=1.0)

        # shape: batch_size , nsteps * anchor_types, 2
        [pred_rpn_prob] = self.sess.run(
                [self.reshape_rpn_prob], feed_dict=fd)

        # shape: batch_size , nsteps * anchor_types
        pred_labels = np.argmax(np.array(pred_rpn_prob), axis = -1)
        return pred_labels, sequence_lengths, pred_rpn_prob

    def run_epoch(self, train, dev, epoch):
        """Performs one complete pass over the train set and evaluate on dev

        Args:
            train: dataset that yields tuple of sentences, tags
            dev: dataset
            epoch: (int) index of the current epoch

        Returns:
            f1: (python float), score to select model on, higher is better

        """
        # progbar stuff for logging
        batch_size = self.config.batch_size
        nbatches = (len(train) + batch_size - 1) // batch_size
        prog = Progbar(target=nbatches)

        train.shuffle_data()
        # print("START HERE")
        # iterate over dataset
        # for i, (words, postags, sentences, anchors, anchor_labels, _, bert_embedding) in enumerate(
        #         minibatches(train, batch_size)):
        for i, (words, postags, anchors, anchor_labels, _, bert_embedding) in enumerate(
                 minibatches(train, batch_size)):
            # print("BERT1",bert_embedding.shape)
            # fd, _ = self.get_feed_dict(words, bert_embedding, postags, sentences, anchors, anchor_labels,
            #         self.config.lr, self.config.dropout)
            fd, _ = self.get_feed_dict(words, bert_embedding, postags, anchors, anchor_labels,
                    self.config.lr, self.config.dropout)
            # print("BERT2",bert_embedding.shape)
            _, train_loss, summary, out_rpn_label, out_rpn_prob  = self.sess.run(
                    [self.train_op, self.loss, self.merged, self.onehot_rpn_label, self.valid_rpn_prob], feed_dict=fd)
            #print(out_rpn_prob)
            prog.update(i + 1, [("train loss", train_loss)])
            # tensorboard
            if i % 10 == 0:
                self.file_writer.add_summary(summary, epoch*nbatches + i)


        metrics = self.run_evaluate(dev)
        msg = " - ".join(["{} {:04.2f}".format(k, v)
                for k, v in metrics.items()])
        self.logger.info(msg)

        return metrics["f1"]

    def dump(self, test, dataset):
        """
        Evaluate rpn on test dataset
        """
        total_roi_feature = []
        #total_roi_elmo_feature = []
        total_roi_bert_feature = []
        total_roi_label = []
        total_roi_len = []
        total_roi = []
        total_words = []
        total_roi_char_ids = []
        total_roi_word_lengths = []
        total_sen_last_hidden = []
        total_context = []
        total_sen_len = []

        total_left_context_feature = []
        total_left_context_len = []
        total_right_context_feature = []
        total_right_context_len = []

        max_word_len = 61

        total_conflict_num = 0
        policy_1_correct = 0

        for words, postags, anchors, anchor_labels, class_ids, bert_embedding in minibatches(
                test, self.config.batch_size):

            fd, sequence_lengths = self.get_feed_dict(words, bert_embedding, postags, dropout=1.0)
            # lstm_1_output
            lstm_feature, batch_last_hidden, batch_context_word, batch_context_len, rpn_prob = self.sess.run(
                    [self.word_embeddings, self.sequence_last_hidden,
                        self.lstm_output, self.sequence_lengths,
                        self.reshape_rpn_prob],
                    feed_dict = fd)
            #elmo_feature = fd[self.elmo_embedding]
            bert_feature = fd[self.bert_embedding]
            char_ids = fd[self.char_ids]
            word_lengths = fd[self.word_lengths]

            # get candidate anchors: roi
            pred_labels = np.argmax(np.array(rpn_prob), axis = -1)
            batch_probs = np.max(np.array(rpn_prob), axis = -1)
            for i in range(len(class_ids)):
                sen_labels = class_ids[i]
                sen_lstm = lstm_feature[i]
                if len(bert_feature[i].shape) != 3:
                    continue
                #sen_elmo = np.transpose(elmo_feature[i], (1, 0, 2))
                sen_bert = np.transpose(bert_feature[i], (1, 0, 2))
                sen_last_hidden = batch_last_hidden[i]
                context_word = batch_context_word[i]
                context_len = batch_context_len[i]

                candi_group = []
                prob_group = []
                cls_group = []
                sen_last_hidden_group = []
                roi_feature_group = []
				
                #roi_elmo_feature_group = []
                roi_bert_feature_group = []
				
                roi_char_ids_group = []
                roi_word_lengths_group = []
                roi_label_group = []
                roi_len_group = []
                left_context_word_group = []
                left_context_len_group = []
                right_context_word_group = []
                right_context_len_group = []

                for j in range(len(sen_labels)):
                    true = sen_labels[j]
                    pred = pred_labels[i][j]
                    if true != -1 and pred == 1:
                        # candidate anchor
                        roi = anchors[i][j]
                        candi_group.append(roi)
                        prob_group.append(batch_probs[i][j])
                        cls_group.append(true)
                        # get feature
                        roi_feature = sen_lstm[roi[0] : roi[1] + 1]
						
                        #roi_elmo_feature = sen_elmo[roi[0] : roi[1] + 1]
                        roi_bert_feature = sen_bert[roi[0] : roi[1] + 1]
						
                        # append roi feature to anchor types
                        anchor_len = roi[1] - roi[0] + 1
                        pad_num = self.config.anchor_types - anchor_len
                        pad_feature = np.zeros((pad_num, self.config.hidden_size_lstm_1 * 2))
						
                        #pad_elmo_feature = np.zeros((pad_num, 3, 1024))
                        pad_bert_feature = np.zeros((pad_num, 3, 1024))
						
                        roi_appended_feature = np.append(roi_feature, pad_feature, axis = 0)
                        #elmo_appended_feature = np.append(roi_elmo_feature, pad_elmo_feature, axis = 0)
                        #elmo_appended_feature = np.transpose(elmo_appended_feature, (1, 0, 2))
                        bert_appended_feature = np.append(roi_bert_feature, pad_bert_feature, axis = 0)
                        bert_appended_feature = np.transpose(bert_appended_feature, (1, 0, 2))
						
                        # add char ids and word lengths
                        valid_char_ids = char_ids[i][roi[0]: roi[1] + 1]
                        pad_char_ids = np.zeros((pad_num, len(char_ids[0][roi[0]])))
                        padded_char_ids = np.append(valid_char_ids, pad_char_ids, axis=0).tolist()
                        valid_word_lengths = word_lengths[i][roi[0]: roi[1] + 1]
                        pad_word_lengths = np.zeros((pad_num))
                        padded_word_lengths = np.append(valid_word_lengths, pad_word_lengths, axis=0)

                        # add context feature
                        left_context_word_group.append(context_word[0:roi[0]])
                        left_context_len_group.append(roi[0])
                        right_context_word_group.append(context_word[roi[1]+1: context_len])
                        right_context_len_group.append(context_len - roi[1] - 1)

                        roi_feature_group.append(roi_appended_feature)
						
                        #roi_elmo_feature_group.append(elmo_appended_feature)
                        roi_bert_feature_group.append(bert_appended_feature)
						
                        roi_char_ids_group.append(padded_char_ids)
                        roi_word_lengths_group.append(padded_word_lengths)
                        roi_label_group.append(true)
                        roi_len_group.append(anchor_len)
                        sen_last_hidden_group.append(sen_last_hidden)

                roi_feature_nonconf, roi_bert_nonconf, roi_label_nonconf, roi_len_nonconf, roi_char_ids_nonconf, roi_word_lengths_nonconf, sen_last_hidden_nonconf, left_context_word_nonconf, left_context_len_nonconf, right_context_word_nonconf, right_context_len_nonconf = detect_conflict(
                        candi_group, prob_group, cls_group, roi_feature_group, 
                        roi_bert_feature_group, roi_label_group, roi_len_group, 
                        roi_char_ids_group, roi_word_lengths_group, sen_last_hidden_group, 
                        left_context_word_group, left_context_len_group, 
                        right_context_word_group, right_context_len_group)

                # add one sentence
                total_roi_feature += roi_feature_nonconf
                #total_roi_elmo_feature += roi_elmo_nonconf
                total_roi_bert_feature += roi_bert_nonconf
                total_roi_label += roi_label_nonconf
                total_roi_len += roi_len_nonconf
                total_roi_char_ids += roi_char_ids_nonconf
                total_roi_word_lengths += roi_word_lengths_nonconf
                total_sen_last_hidden += sen_last_hidden_nonconf
                total_left_context_feature += left_context_word_nonconf
                total_left_context_len += left_context_len_nonconf
                total_right_context_feature += right_context_word_nonconf
                total_right_context_len += right_context_len_nonconf

        prefix_dir  = self.config.dir_saved_roi+dataset+'_word_ids'
        print("dump anchor features into ", prefix_dir)
        if not os.path.exists(prefix_dir):
            os.makedirs(prefix_dir)
        np.save(prefix_dir+'/roi_features', np.array(total_roi_feature))
        #np.save(prefix_dir+'/roi_elmo_features', np.array(total_roi_elmo_feature))
        np.save(prefix_dir+'/roi_bert_features', np.array(total_roi_bert_feature))
        np.save(prefix_dir+'/roi_labels', np.array(total_roi_label))
        np.save(prefix_dir+'/roi_lens', np.array(total_roi_len))
        np.save(prefix_dir+'/roi_char_ids', np.array(total_roi_char_ids))
        np.save(prefix_dir+'/roi_word_lengths', np.array(total_roi_word_lengths))
        np.save(prefix_dir+'/sen_last_hidden', np.array(total_sen_last_hidden))
        np.save(prefix_dir+'/left_context_word_feature', np.array(total_left_context_feature))
        np.save(prefix_dir+'/left_context_word_len', np.array(total_left_context_len))
        np.save(prefix_dir+'/right_context_word_feature', np.array(total_right_context_feature))
        np.save(prefix_dir+'/right_context_word_len', np.array(total_right_context_len))

    def run_evaluate(self, test):
        """
        Evaluate rpn on test dataset
        """
        true_pos, false_pos, true_neg, false_neg = 0, 0, 0, 0
        batch_idx = 0
        # for words, postags, sentences, anchors, anchor_labels, _, bert_embedding in minibatches(
        #         test, self.config.batch_size):
        for words, postags, anchors, anchor_labels, _, bert_embedding in minibatches(
                test, self.config.batch_size):
            # pred_labels, seq_lengths, batch_prob = self.predict_rpn(words, bert_embedding,postags, sentences)
            pred_labels, seq_lengths, batch_prob = self.predict_rpn(words, bert_embedding,postags)

            for line_idx in range(len(anchor_labels)):
                line_label = anchor_labels[line_idx]
                line_pred = pred_labels[line_idx]

                for i in range(len(line_label)):
                    if line_label[i] == 1 and line_pred[i] == 1:
                        true_pos += 1
                    if line_label[i] == 1 and line_pred[i] == 0:
                        false_neg += 1
                    if line_label[i] == 0 and line_pred[i] == 0:
                        true_neg += 1
                    if line_label[i] == 0 and line_pred[i] == 1:
                        false_pos += 1

            if (batch_idx % 10 == 0):
                print("true_pos", true_pos, "false_neg", false_neg,
                        "true_neg", true_neg, "false_pos", false_pos)
            batch_idx += 1

        total = true_pos + false_pos + true_neg + false_neg
        precision = float(true_pos) / (true_pos + false_pos)
        recall = float(true_pos) / (true_pos + false_neg)
        acc = (true_pos + true_neg) / float(total)
        f1 = 2 * precision * recall / (precision + recall)
        print("total", total, "true_pos", true_pos, "false_pos", false_pos,
                "true_neg", true_neg, "false_neg", false_neg)
        print("precision", precision, "recall", recall, "f1", f1)

        return {"precision": 100 * precision,
                "recall": 100 * recall,
                "acc": 100 * acc,
                "f1": 100 * f1}
